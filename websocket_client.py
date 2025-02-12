import argparse
import asyncio
import datetime
import json
import logging
import os
import signal
import sys
import urllib.parse
from typing import Any, AsyncGenerator, Awaitable, Literal

import aiohttp
import numpy as np
import pyee.asyncio
import requests
import sounddevice
from websockets import exceptions as ws_exceptions
from websockets import client as ws_client
from dotenv import load_dotenv
from firebase_service import FirebaseService
from database_tools import DatabaseTools, FirebaseEncoder

# Load environment variables from .env file
load_dotenv()

# Initialize Firebase service and database tools
firebase = FirebaseService()
db_tools = DatabaseTools()

class LocalAudioSink:
    """
    A sink for audio. Buffered audio is played using the default audio device.

    Args:
        sample_rate: The sample rate to use for audio playback. Defaults to 48kHz.
    """

    def __init__(self, sample_rate: int = 48000) -> None:
        self._sample_rate = sample_rate
        self._buffer: bytearray = bytearray()

        def callback(outdata: np.ndarray, frame_count, time, status):
            output_frame_size = len(outdata) * 2
            next_frame = self._buffer[:output_frame_size]
            self._buffer[:] = self._buffer[output_frame_size:]
            if len(next_frame) < output_frame_size:
                next_frame += b"\x00" * (output_frame_size - len(next_frame))
            outdata[:] = np.frombuffer(next_frame, dtype="int16").reshape(
                (frame_count, 1)
            )

        self._stream = sounddevice.OutputStream(
            samplerate=sample_rate,
            channels=1,
            callback=callback,
            device=None,
            dtype="int16",
            blocksize=sample_rate // 100,
        )
        self._stream.start()
        if not self._stream.active:
            raise RuntimeError("Failed to start streaming output audio")

    def write(self, chunk: bytes) -> None:
        """Writes audio data (expected to be in 16-bit PCM format) to this sink's buffer."""
        self._buffer.extend(chunk)

    def drop_buffer(self) -> None:
        """Drops all audio data in this sink's buffer, ending playback until new data is written."""
        self._buffer.clear()

    async def close(self) -> None:
        if self._stream:
            self._stream.close()


class LocalAudioSource:
    """
    A source for audio data that reads from the default microphone. Audio data in
    16-bit PCM format is available as an AsyncGenerator via the `stream` method.

    Args:
        sample_rate: The sample rate to use for audio recording. Defaults to 48kHz.
    """

    def __init__(self, sample_rate=48000):
        self._sample_rate = sample_rate

    async def stream(self) -> AsyncGenerator[bytes, None]:
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def callback(indata: np.ndarray, frame_count, time, status):
            loop.call_soon_threadsafe(queue.put_nowait, indata.tobytes())

        stream = sounddevice.InputStream(
            samplerate=self._sample_rate,
            channels=1,
            callback=callback,
            device=None,
            dtype="int16",
            blocksize=self._sample_rate // 100,
        )
        with stream:
            if not stream.active:
                raise RuntimeError("Failed to start streaming input audio")
            while True:
                yield await queue.get()


class WebsocketVoiceSession(pyee.asyncio.AsyncIOEventEmitter):
    """A websocket-based voice session that connects to an Ultravox call."""

    def __init__(self, join_url: str):
        super().__init__()
        self._state: Literal["idle", "listening", "thinking", "speaking"] = "idle"
        self._pending_output = ""
        self._url = join_url
        self._socket = None
        self._receive_task: asyncio.Task | None = None
        self._send_audio_task = asyncio.create_task(
            self._pump_audio(LocalAudioSource())
        )
        self._sink = LocalAudioSink()
        self._use_flask_callback = bool(os.getenv('FLASK_STATE_CALLBACK'))

    def _update_state(self, new_state: str):
        if new_state != self._state:
            self._state = new_state
            self.emit("state", new_state)
            if self._use_flask_callback:
                try:
                    requests.get(f'http://localhost:5000/update_state/{new_state}')
                except Exception as e:
                    logging.warning(f"Failed to update Flask state: {e}")

    async def start(self):
        """Start the websocket session."""
        logging.info(f"Connecting to {self._url}")
        self._socket = await ws_client.connect(self._url)
        self._receive_task = asyncio.create_task(self._socket_receive(self._socket))

    async def _socket_receive(self, socket: ws_client.ClientConnection):
        try:
            async for message in socket:
                await self._on_socket_message(message)
        except asyncio.CancelledError:
            logging.info("socket cancelled")
        except ws_exceptions.ConnectionClosedOK:
            logging.info("socket closed ok")
        except ws_exceptions.ConnectionClosedError as e:
            self.emit("error", e)
            return
        logging.info("socket receive done")
        self.emit("ended")

    async def stop(self):
        """End the session, closing the connection and ending the call."""
        logging.info("Stopping...")
        await _async_close(
            self._sink.close(),
            self._socket.close() if self._socket else None,
            _async_cancel(self._send_audio_task, self._receive_task),
        )
        if self._state != "idle":
            self._update_state("idle")

    async def _on_socket_message(self, payload: str | bytes):
        if isinstance(payload, bytes):
            self._sink.write(payload)
            return
        elif isinstance(payload, str):
            msg = json.loads(payload)
            await self._handle_data_message(msg)

    async def _handle_data_message(self, msg: dict[str, Any]):
        match msg["type"]:
            case "playback_clear_buffer":
                self._sink.drop_buffer()
            case "state":
                self._update_state(msg["state"])
            case "transcript":
                # Handle only agent transcripts, ignore user transcripts
                if msg["role"] != "agent":
                    return
                if msg.get("text", None):
                    self._pending_output = msg["text"]
                    self.emit("output", msg["text"], msg["final"])
                else:
                    self._pending_output += msg.get("delta", "")
                    self.emit("output", self._pending_output, msg["final"])
                if msg["final"]:
                    self._pending_output = ""
            case "voice_synced_transcript":
                # Just ignore voice synced transcripts
                pass
            case "client_tool_invocation":
                await self._handle_client_tool_call(
                    msg["toolName"], msg["invocationId"], msg["parameters"]
                )
            case "debug":
                logging.info(f"debug: {msg['message']}")
            case _:
                logging.warning(f"Unhandled message type: {msg['type']}")

    async def _handle_client_tool_call(
        self, tool_name: str, invocation_id: str, parameters: dict[str, Any]
    ):
        logging.info(f"client tool call: {tool_name}")
        response: dict[str, str] = {
            "type": "client_tool_result",
            "invocationId": invocation_id,
        }
        
        try:
            # User operations
            if tool_name == "getUser":
                result = db_tools.get_user(parameters["userId"])
                response["result"] = json.dumps(result, cls=FirebaseEncoder) if result is not None else None
                
            elif tool_name == "getAllUsers":
                result = db_tools.get_all_users()
                response["result"] = json.dumps(result, cls=FirebaseEncoder)

            # Customer operations
            elif tool_name == "getCustomer":
                result = db_tools.get_customer(parameters["customerId"])
                response["result"] = json.dumps(result, cls=FirebaseEncoder) if result is not None else None
                
            elif tool_name == "getAllCustomers":
                result = db_tools.get_all_customers()
                response["result"] = json.dumps(result, cls=FirebaseEncoder)
                
            elif tool_name == "updateCustomer":
                success = db_tools.update_customer(
                    parameters["customerId"],
                    json.loads(parameters["data"])
                )
                response["result"] = json.dumps({"success": success}, cls=FirebaseEncoder)

            # Conversation operations
            elif tool_name == "getConversation":
                result = db_tools.get_conversation(parameters["conversationId"])
                response["result"] = json.dumps(result, cls=FirebaseEncoder) if result is not None else None
                
            elif tool_name == "getUltravoxConversation":
                result = db_tools.get_ultravox_conversation(parameters["conversationId"])
                response["result"] = json.dumps(result, cls=FirebaseEncoder) if result is not None else None
                
            elif tool_name == "getAllConversations":
                result = db_tools.get_all_conversations()
                response["result"] = json.dumps(result, cls=FirebaseEncoder)
                
            elif tool_name == "getAllUltravoxConversations":
                result = db_tools.get_all_ultravox_conversations()
                response["result"] = json.dumps(result, cls=FirebaseEncoder)
                
            elif tool_name == "saveConversation":
                doc_id = db_tools.save_conversation(
                    json.loads(parameters["data"]),
                    bool(parameters.get("isUltravox", False))
                )
                response["result"] = json.dumps({"conversationId": doc_id}, cls=FirebaseEncoder) if doc_id else None

            # Service status operations
            elif tool_name == "getServiceStatus":
                result = db_tools.get_service_status(parameters.get("statusId", "416"))
                response["result"] = json.dumps(result, cls=FirebaseEncoder) if result is not None else None
                
            elif tool_name == "updateServiceStatus":
                success = db_tools.update_service_status(
                    parameters.get("statusId", "416"),
                    json.loads(parameters["data"])
                )
                response["result"] = json.dumps({"success": success}, cls=FirebaseEncoder)

            # Tariff operations
            elif tool_name == "getTariff":
                result = db_tools.get_tariff(parameters["tariffId"])
                response["result"] = json.dumps(result, cls=FirebaseEncoder) if result is not None else None
                
            elif tool_name == "getAllTariffs":
                result = db_tools.get_all_tariffs()
                response["result"] = json.dumps(result, cls=FirebaseEncoder)
                
            elif tool_name == "getResidentialTariff":
                result = db_tools.get_residential_tariff()
                response["result"] = json.dumps(result, cls=FirebaseEncoder) if result is not None else None

            # Database exploration
            elif tool_name == "exploreDatabase":
                result = db_tools.explore_database()
                response["result"] = json.dumps(result, cls=FirebaseEncoder)
                
            else:
                response["errorType"] = "undefined"
                response["errorMessage"] = f"Unknown tool: {tool_name}"
                
        except Exception as e:
            response["errorType"] = type(e).__name__
            response["errorMessage"] = str(e)
            
        await self._socket.send(json.dumps(response))

    async def _pump_audio(self, source: LocalAudioSource):
        async for chunk in source.stream():
            if self._socket is None:
                continue
            await self._socket.send(chunk)


async def _async_close(*awaitables_or_none: Awaitable | None):
    coros = [coro for coro in awaitables_or_none if coro is not None]
    if coros:
        maybe_exceptions = await asyncio.shield(
            asyncio.gather(*coros, return_exceptions=True)
        )
        non_cancelled_exceptions = [
            exc
            for exc in maybe_exceptions
            if isinstance(exc, Exception)
            and not isinstance(exc, asyncio.CancelledError)
        ]
        if non_cancelled_exceptions:
            to_report = (
                non_cancelled_exceptions[0]
                if len(non_cancelled_exceptions) == 1
                else ExceptionGroup("Multiple failures", non_cancelled_exceptions)
            )
            logging.warning("Error during _async_close", exc_info=to_report)


async def _async_cancel(*tasks_or_none: asyncio.Task | None):
    tasks = [task for task in tasks_or_none if task is not None and task.cancel()]
    await _async_close(*tasks)


async def _get_join_url() -> str:
    """Creates a new call, returning its join URL."""
    target = "https://api.ultravox.ai/api/calls"
    if args.prior_call_id:
        target += f"?priorCallId={args.prior_call_id}"
    async with aiohttp.ClientSession() as session:
        headers = {"X-API-Key": f"{os.getenv('ULTRAVOX_API_KEY', None)}"}
        system_prompt = args.system_prompt
        selected_tools = [
            {
                "temporaryTool": {
                    "modelToolName": "getUser",
                    "description": "Get user information by ID",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "getAllUsers",
                    "description": "Get all users from the database",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "getCustomer",
                    "description": "Get customer information by ID",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "getAllCustomers",
                    "description": "Get all customers from the database",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "updateCustomer",
                    "description": "Update customer information",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "getConversation",
                    "description": "Get conversation details by ID",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "getAllConversations",
                    "description": "Get all conversations from the database",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "getUltravoxConversation",
                    "description": "Get Ultravox conversation details by ID",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "getAllUltravoxConversations",
                    "description": "Get all Ultravox conversations from the database",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "saveConversation",
                    "description": "Save a new conversation to the database",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "getServiceStatus",
                    "description": "Get current service status",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "updateServiceStatus",
                    "description": "Update service status information",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "getTariff",
                    "description": "Get tariff information by ID",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "getAllTariffs",
                    "description": "Get all available tariffs",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "getResidentialTariff",
                    "description": "Get the residential standard tariff",
                    "client": {},
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "exploreDatabase",
                    "description": "Get an overview of all collections and documents",
                    "client": {},
                },
            },
        ]
        if args.secret_menu:
            system_prompt += "\n\nThere is also a secret menu that changes daily. If the user asks about it, use the getSecretMenu tool to look up today's secret menu items."
            selected_tools.append(
                {
                    "temporaryTool": {
                        "modelToolName": "getSecretMenu",
                        "description": "Looks up today's secret menu items.",
                        "client": {},
                    },
                }
            )
        body = {
            "systemPrompt": system_prompt,
            "temperature": args.temperature,
            "medium": {
                "serverWebSocket": {
                    "inputSampleRate": 48000,
                    "outputSampleRate": 48000,
                    "clientBufferSizeMs": 30000,
                }
            },
            "selectedTools": selected_tools,  # Always include our database tools
        }
        if args.voice:
            body["voice"] = args.voice
        if args.initial_output_text:
            body["initialOutputMedium"] = "MESSAGE_MEDIUM_TEXT"
        if args.user_speaks_first:
            body["firstSpeaker"] = "FIRST_SPEAKER_USER"

        logging.info(f"Creating call with body: {body}")
        async with session.post(target, headers=headers, json=body) as response:
            response.raise_for_status()
            response_json = await response.json()
            join_url = response_json["joinUrl"]
            join_url = _add_query_param(
                join_url, "apiVersion", str(args.api_version or 1)
            )
            if args.experimental_messages:
                join_url = _add_query_param(
                    join_url, "experimentalMessages", args.experimental_messages
                )
            return join_url


def _add_query_param(url: str, key: str, value: str) -> str:
    url_parts = list(urllib.parse.urlparse(url))
    query = dict(urllib.parse.parse_qsl(url_parts[4]))
    query.update({key: value})
    url_parts[4] = urllib.parse.urlencode(query)
    return urllib.parse.urlunparse(url_parts)


async def main():
    join_url = await _get_join_url()
    client = WebsocketVoiceSession(join_url)
    done = asyncio.Event()
    loop = asyncio.get_running_loop()

    @client.on("state")
    async def on_state(state):
        if state == "listening":
            print("User:  ", end="\r")
        elif state == "thinking":
            print("Agent: ", end="\r")

    @client.on("output")
    async def on_output(text, final):
        display_text = f"{text.strip()}"
        print("Agent: " + display_text, end="\n" if final else "\r")

    @client.on("error")
    async def on_error(error):
        logging.exception("Client error", exc_info=error)
        print(f"Error: {error}")
        done.set()

    @client.on("ended")
    async def on_ended():
        print("Session ended")
        done.set()

    try:
        # Try to add signal handlers for Unix systems
        loop.add_signal_handler(signal.SIGINT, lambda: done.set())
        loop.add_signal_handler(signal.SIGTERM, lambda: done.set())
    except (NotImplementedError, AttributeError):
        # On Windows, we'll handle keyboard interrupt directly
        def windows_signal_handler(signum, frame):
            done.set()
        signal.signal(signal.SIGINT, windows_signal_handler)

    await client.start()
    await done.wait()
    await client.stop()


if __name__ == "__main__":
    api_key = os.getenv("ULTRAVOX_API_KEY", None)
    if not api_key:
        raise ValueError("Please set your ULTRAVOX_API_KEY environment variable")

    parser = argparse.ArgumentParser(prog="websocket_client.py")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show verbose session information"
    )
    parser.add_argument(
        "--very-verbose", "-vv", action="store_true", help="Show debug logs too"
    )
    parser.add_argument("--voice", "-V", type=str, help="Name (or id) of voice to use")
    parser.add_argument(
        "--system-prompt",
        "-s",
        type=str,
        default="""You are an AI assistant that ONLY provides information directly from our Firebase database. You must NEVER make up or hallucinate any information. Local time is currently: ${datetime.datetime.now().isoformat()}

Your role is to:
1. ONLY retrieve and present information that exists in our Firebase database
2. If information is not found in the database, clearly state that it's not available
3. Never make assumptions or create fictional data

Available Database Collections and Tools:
1. Tariffs Collection:
   - Use getAllTariffs to view actual tariff records
   - Use getTariff with a specific ID to get tariff details
   - Use getResidentialTariff for standard residential rates

2. Service Status:
   - Use getServiceStatus to check current system status
   - Only report actual status from the database

3. Customer Records:
   - Use getCustomer to look up verified customer information
   - Use getAllCustomers to view existing customer records

4. User Information:
   - Use getUser for specific user details
   - Use getAllUsers to view user records

5. Conversation Records:
   - Use getConversation and getUltravoxConversation for specific conversations
   - Use getAllConversations and getAllUltravoxConversations for all records
   - Use saveConversation to record new conversations

6. Database Overview:
   - Use exploreDatabase to see all available collections

IMPORTANT RULES:
- ONLY provide information that you can verify exists in the database
- If asked about information that isn't in the database, say "I don't have that information in the database"
- Do not make up or infer information that isn't explicitly in the database
- When using tools, always check if the data exists before making statements about it
- If a database query returns null or empty, clearly communicate this to the user

Example responses:
- If data exists: "According to our database, the current tariff is [exact data from database]"
- If data doesn't exist: "I've checked the database, but I don't have that information available"
- If unsure: "Let me check the database for that information" then use the appropriate tool

Remember: Your responses must be based SOLELY on actual database content. Never invent or assume information.""",
        help="System prompt to use when creating the call",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.8,
        help="Temperature to use when creating the call",
    )
    parser.add_argument(
        "--secret-menu",
        action="store_true",
        help="Adds prompt and client-implemented tool for a secret menu",
    )
    parser.add_argument(
        "--experimental-messages",
        type=str,
        help="Enables the specified experimental messages",
    )
    parser.add_argument(
        "--prior-call-id",
        type=str,
        help="Allows setting priorCallId during start call",
    )
    parser.add_argument(
        "--user-speaks-first",
        action="store_true",
        help="If set, sets FIRST_SPEAKER_USER",
    )
    parser.add_argument(
        "--initial-output-text",
        action="store_true",
        help="Sets the initial_output_medium to text",
    )
    parser.add_argument(
        "--api-version",
        type=int,
        help="API version to set when creating the call.",
    )

    args = parser.parse_args()
    if args.very_verbose:
        logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
    elif args.verbose:
        logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    asyncio.run(main())
