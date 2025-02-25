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

# Define stage prompts as a global constant
STAGE_PROMPTS = {
    "authentication": """Du bist Gourmet, der KI-Assistent für Feudenheim Energie.
    Du bist zurzeit in der Stage Authentication.
    In dieser Stage kannst du Kunden allgemeine Informationen über die Feudenheim Energie AG und deren Produkte geben.
    Wenn der Kunde eine Anfrage hat, die sein Kundenprofil involviert muss er erst verifiziert werden.
    Wenn der Kunde die Kundennummer nennt, bitte mit getCustomer nachprüfen ob die Nummer korrekt ist.
    Wenn der Kunde eine Frage hat wie welche Tarife es gibt, bitte mit getAllTariffs nachprüfen welche Tarife es gibt.

===INTERNAL_INSTRUCTIONS===
- Prüfen Sie die genannte Kundennummer EXAKT wie vom Kunden angegeben mit getCustomer
- Fügen Sie KEINE zusätzlichen Ziffern hinzu
- Sprechen Sie die Kundennummer zur Bestätigung EINZELN IN DEUTSCH LANGSAM aus (z.B. "vier-zwei-drei")
- Warten Sie auf Bestätigung vom Kunden, dass die Nummer korrekt ist
- Bei Fehler oder Verneinung: Höflich um erneute Nennung der kompletten Nummer bitten
- Bei erfolgreicher Prüfung und Bestätigung direkt das Tool changeStage zu customer_service aufrufen. NICHTS SAGEN DA DER NÄCHSTE AGENT REDET.
- Bleiben Sie stets höflich und professionell
- JEDE KUNDENUMMER BEGINNT MIT 4300XXXXXXXXX""",
    
    "customer_service": """Der Kunde ist nun verifiziert Sag dass du nun Siffy der Level 2 Agent heißt und ihm helfen kannst. 
    Frage noch einmal: Habe ich ihre Anfrage richtig verstanden?

===INTERNAL_INSTRUCTIONS===
- Nutzen Sie die verifizierten Kundendaten für personalisierte Antworten
- Verwenden Sie AUSSCHLIESSLICH die bestätigte Kundennummer für alle Operationen
- Bei Abschlagsfragen: Sprechen Sie den aktuellen Betrag in deutscher Wortform aus. Hundertfünfundzwanzig Euro = 125, dreiundsechzig Euro = 63
- Bei unklarer Anfrage: Höflich erneut nachfragen
- Bleiben Sie stets höflich und professionell""",
    
    "abschlag_management": """Der Kunde möchte seinen Abschlag ändern.
    Sage ihm erst:
    Ihr aktueller Abschlag beträgt {current_amount} Euro. Welchen neuen Betrag möchten Sie festlegen?

===INTERNAL_INSTRUCTIONS===
- Nutzen Sie updateAbschlag nur mit den verifizierten Kundendaten
- Wiederholen Sie den genannten Betrag zur Bestätigung
- Sprechen Sie Beträge immer in korrektem Deutsch aus Hundertfünfundzwanzig Euro = 125, dreiundsechzig Euro = 63
- Nach erfolgreicher Änderung: Bestätigen Sie die Änderung mit dem Betrag in Worten
- Fragen Sie "Kann ich sonst noch etwas für Sie tun?"
- Bei weiteren Anliegen: Direkt nach dem neuen Anliegen fragen
- Bleiben Sie stets höflich und professionell"""}

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
        self._stream = None
        self._setup_stream()

    def _setup_stream(self):
        try:
            def callback(outdata: np.ndarray, frame_count, time, status):
                if status:
                    logging.warning(f'Audio output status: {status}')
                output_frame_size = len(outdata) * 2
                next_frame = self._buffer[:output_frame_size]
                self._buffer[:] = self._buffer[output_frame_size:]
                if len(next_frame) < output_frame_size:
                    next_frame += b"\x00" * (output_frame_size - len(next_frame))
                outdata[:] = np.frombuffer(next_frame, dtype="int16").reshape(
                    (frame_count, 1)
                )

            self._stream = sounddevice.OutputStream(
                samplerate=self._sample_rate,
                channels=1,
                callback=callback,
                device=None,
                dtype="int16",
                blocksize=self._sample_rate // 100,
            )
            self._stream.start()
            if not self._stream.active:
                raise RuntimeError("Failed to start streaming output audio")
            logging.info("Audio output stream initialized successfully")
        except Exception as e:
            logging.error(f"Error setting up audio output stream: {e}")
            raise

    def write(self, chunk: bytes) -> None:
        """Writes audio data (expected to be in 16-bit PCM format) to this sink's buffer."""
        try:
            self._buffer.extend(chunk)
        except Exception as e:
            logging.error(f"Error writing to audio buffer: {e}")

    def drop_buffer(self) -> None:
        """Drops all audio data in this sink's buffer, ending playback until new data is written."""
        try:
            self._buffer.clear()
            logging.debug("Audio buffer cleared")
        except Exception as e:
            logging.error(f"Error clearing audio buffer: {e}")

    async def close(self) -> None:
        try:
            if self._stream:
                self._stream.close()
                logging.info("Audio output stream closed")
        except Exception as e:
            logging.error(f"Error closing audio stream: {e}")


class LocalAudioSource:
    """
    A source for audio data that reads from the default microphone. Audio data in
    16-bit PCM format is available as an AsyncGenerator via the `stream` method.

    Args:
        sample_rate: The sample rate to use for audio recording. Defaults to 48kHz.
    """

    def __init__(self, sample_rate=48000):
        self._sample_rate = sample_rate
        self._stream = None
        self._running = True
        # Audio processing settings
        self._threshold = 0.01  # Will be set by command line arg
        self._min_audio_duration = 0.35  # Longer duration for stability
        self._consecutive_frames = 0
        self._frames_threshold = int(self._min_audio_duration * (sample_rate / (sample_rate // 100)))
        self._last_audio_time = 0
        self._min_silence_duration = 1.0  # Longer silence for better switching
        # Audio level processing
        self._audio_level_history = []
        self._history_size = 12  # Larger window for stability
        self._noise_floor = None  # Initialize as None
        self._noise_floor_alpha = 0.98  # Slower noise floor adaptation
        self._noise_multiplier = 2.5  # Higher noise floor impact
        self._threshold_multiplier = 3.0  # Stricter threshold
        self._peak_level = 0.0  # Track peak levels
        self._peak_alpha = 0.99  # Slower peak adaptation
        self._initial_calibration_frames = 50  # More calibration frames
        self._calibration_count = 0
        self._debug_audio = True
        # Print available devices
        self._print_audio_devices()

    def _print_audio_devices(self):
        try:
            devices = sounddevice.query_devices()
            default_input = sounddevice.query_devices(kind='input')
            default_output = sounddevice.query_devices(kind='output')
            
            logging.info("=== Audio Device Configuration ===")
            logging.info(f"Default Input Device: {default_input['name']} (ID: {default_input['index']})")
            logging.info(f"Default Output Device: {default_output['name']} (ID: {default_output['index']})")
            logging.info("=== Available Audio Devices ===")
            for i, dev in enumerate(devices):
                logging.info(f"Device {i}: {dev['name']} ({'input' if dev['max_input_channels'] > 0 else 'output'})")
        except Exception as e:
            logging.error(f"Error querying audio devices: {e}")

    async def close(self):
        """Cleanup audio resources."""
        self._running = False
        try:
            if self._stream:
                self._stream.close()
                self._stream = None
                logging.info("Audio input stream closed")
        except Exception as e:
            logging.error(f"Error closing audio stream: {e}")

    async def stream(self) -> AsyncGenerator[bytes, None]:
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def callback(indata: np.ndarray, frame_count, time, status):
            if status:
                logging.warning(f'Audio input status: {status}')
            try:
                # Calculate RMS with improved noise handling
                squared = np.square(indata.astype(np.float64))
                mean_squared = np.mean(squared) if squared.size > 0 else 0
                audio_level = np.sqrt(max(0, mean_squared))
                
                # Initialize or update noise floor during calibration
                if self._calibration_count < self._initial_calibration_frames:
                    if self._noise_floor is None:
                        self._noise_floor = audio_level
                    else:
                        self._noise_floor = 0.98 * self._noise_floor + 0.02 * audio_level
                    self._calibration_count += 1
                    return
                
                # Update peak level and noise floor
                self._peak_level = max(audio_level, self._peak_level * self._peak_alpha)
                if audio_level < self._peak_level * 0.2:  # Only update noise floor with quiet audio
                    self._noise_floor = self._noise_floor_alpha * self._noise_floor + (1 - self._noise_floor_alpha) * audio_level
                
                # Add to history and calculate moving average
                self._audio_level_history.append(audio_level)
                if len(self._audio_level_history) > self._history_size:
                    self._audio_level_history.pop(0)
                
                # Calculate average excluding outliers
                sorted_levels = sorted(self._audio_level_history)
                trimmed_levels = sorted_levels[1:-1] if len(sorted_levels) > 2 else sorted_levels
                avg_audio_level = np.mean(trimmed_levels) if trimmed_levels else 0
                
                current_time = time.currentTime if time else 0
                
                # Calculate effective threshold using base threshold and noise floor
                base_threshold = self._threshold * self._threshold_multiplier
                noise_threshold = self._noise_floor * self._noise_multiplier
                effective_threshold = max(base_threshold, noise_threshold)
                
                # More stringent audio detection
                if (not np.isnan(avg_audio_level) and 
                    avg_audio_level > effective_threshold and 
                    avg_audio_level > self._noise_floor * 3.0 and  # Must be well above noise
                    avg_audio_level > self._peak_level * 0.2):  # Must be significant compared to peak
                    
                    self._consecutive_frames += 1
                    if self._consecutive_frames >= self._frames_threshold:
                        loop.call_soon_threadsafe(queue.put_nowait, indata.tobytes())
                        if self._debug_audio:
                            logging.info(f"Audio: {avg_audio_level:.1f}, Peak: {self._peak_level:.1f}, Thresh: {effective_threshold:.1f}, Noise: {self._noise_floor:.1f}")
                        self._last_audio_time = current_time
                else:
                    if current_time - self._last_audio_time > self._min_silence_duration:
                        self._consecutive_frames = 0
                        # Slower peak level decay
                        self._peak_level *= 0.95
                        if len(self._audio_level_history) > 2:
                            self._audio_level_history = self._audio_level_history[-2:]
            except Exception as e:
                logging.error(f"Error in audio input callback: {e}")
                self._consecutive_frames = 0

        try:
            # Get default input device info
            device_info = sounddevice.query_devices(kind='input')
            logging.info(f"Using input device: {device_info['name']} with {device_info['max_input_channels']} channels")
            logging.info(f"Audio settings: threshold={self._threshold}, min_duration={self._min_audio_duration}s, silence_duration={self._min_silence_duration}s")
            
            self._stream = sounddevice.InputStream(
                samplerate=self._sample_rate,
                channels=1,
                callback=callback,
                device=None,  # Use default device
                dtype="int16",
                blocksize=self._sample_rate // 100,
                latency='high'  # Use high latency for better noise filtering
            )
            
            with self._stream:
                if not self._stream.active:
                    raise RuntimeError("Failed to start streaming input audio")
                logging.info(f"Audio input stream initialized successfully with threshold {self._threshold}")
                while self._running:
                    try:
                        yield await queue.get()
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        if self._running:
                            logging.error(f"Error getting audio data from queue: {e}")
                        break
        except Exception as e:
            logging.error(f"Error in audio input stream: {e}")
            raise
        finally:
            await self.close()


class WebsocketVoiceSession(pyee.asyncio.AsyncIOEventEmitter):
    """A websocket-based voice session that connects to an Ultravox call."""

    def __init__(self, join_url: str):
        super().__init__()
        self._state: Literal["idle", "listening", "thinking", "speaking"] = "idle"
        self._pending_output = ""
        self._url = join_url
        self._socket = None
        self._receive_task: asyncio.Task | None = None
        self._send_audio_task: asyncio.Task | None = None
        self._sink = LocalAudioSink()
        self._audio_source = None
        self._running = True
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
        self._running = True
        await self._connect()

    async def _connect(self):
        """Establish websocket connection with retry logic."""
        retry_count = 0
        max_retries = 3
        retry_delay = 2  # seconds

        while self._running and retry_count < max_retries:
            try:
                logging.info(f"Connecting to {self._url} (attempt {retry_count + 1}/{max_retries})")
                self._socket = await ws_client.connect(self._url)
                self._receive_task = asyncio.create_task(self._socket_receive(self._socket))
                
                # Initialize audio source only after successful connection
                if not self._audio_source:
                    self._audio_source = LocalAudioSource()
                    if hasattr(args, 'mic_threshold'):
                        self._audio_source._threshold = args.mic_threshold
                
                self._send_audio_task = asyncio.create_task(self._pump_audio(self._audio_source))
                
                logging.info("Connection established successfully")
                return True
            except Exception as e:
                logging.error(f"Connection attempt {retry_count + 1} failed: {e}")
                retry_count += 1
                if retry_count < max_retries:
                    logging.info(f"Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
        
        logging.error("Failed to establish connection after maximum retries")
        return False

    async def stop(self):
        """End the session, closing the connection and ending the call."""
        self._running = False
        logging.info("Stopping session...")
        if self._audio_source:
            await self._audio_source.close()
        await _async_close(
            self._sink.close(),
            self._socket.close() if self._socket else None,
            _async_cancel(self._send_audio_task, self._receive_task),
        )
        if self._state != "idle":
            self._update_state("idle")

    async def _socket_receive(self, socket: ws_client.ClientConnection):
        try:
            async for message in socket:
                try:
                    await self._on_socket_message(message)
                except Exception as e:
                    logging.error(f"Error processing message: {e}")
        except asyncio.CancelledError:
            logging.info("Socket receive cancelled")
        except ws_exceptions.ConnectionClosedOK:
            logging.info("Socket closed normally")
        except ws_exceptions.ConnectionClosedError as e:
            logging.error(f"Socket closed with error: {e}")
            self.emit("error", e)
            return
        except Exception as e:
            logging.error(f"Unexpected error in socket receive: {e}")
        finally:
            logging.info("Socket receive completed")
            self.emit("ended")

    async def _on_socket_message(self, payload: str | bytes):
        if isinstance(payload, bytes):
            try:
                self._sink.write(payload)
            except Exception as e:
                logging.error(f"Error writing to audio sink: {e}")
            return
        elif isinstance(payload, str):
            try:
                msg = json.loads(payload)
                await self._handle_data_message(msg)
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding message: {e}")
            except Exception as e:
                logging.error(f"Error handling message: {e}")

    async def _handle_data_message(self, msg: dict[str, Any]):
        try:
            match msg["type"]:
                case "playback_clear_buffer":
                    self._sink.drop_buffer()
                    logging.debug("Audio buffer cleared")
                case "state":
                    old_state = self._state
                    self._update_state(msg["state"])
                    logging.info(f"State changed from {old_state} to {msg['state']}")
                case "transcript":
                    # Handle only agent transcripts, ignore user transcripts
                    if msg["role"] != "agent":
                        if msg.get("final", False):
                            logging.info(f"User input (final): {msg.get('text', '')}")
                        return
                    if msg.get("text", None):
                        self._pending_output = msg["text"]
                        self.emit("output", msg["text"], msg["final"])
                        if msg["final"]:
                            logging.info(f"Agent output (final): {msg['text']}")
                    else:
                        self._pending_output += msg.get("delta", "")
                        self.emit("output", self._pending_output, msg["final"])
                    if msg["final"]:
                        self._pending_output = ""
                case "voice_synced_transcript":
                    logging.debug("Received voice sync transcript")
                    pass
                case "client_tool_invocation":
                    logging.info(f"Handling tool call: {msg['toolName']}")
                    await self._handle_client_tool_call(
                        msg["toolName"], msg["invocationId"], msg["parameters"]
                    )
                case "debug":
                    logging.info(f"Debug message: {msg['message']}")
                case _:
                    logging.warning(f"Unhandled message type: {msg['type']}")
        except Exception as e:
            logging.error(f"Error in handle_data_message: {e}")

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
                try:
                    kundennummer = parameters.get("kundennummer")
                    logging.info(f"Looking up customer with Kundennummer: {kundennummer}")
                    result = db_tools.get_customer(kundennummer)
                    if result:
                        logging.info("Customer found")
                        # Prepare stage change with customer data
                        stage_response = {
                            "type": "client_tool_result",
                            "responseType": "new-stage",
                            "body": {
                                "systemPrompt": """Guten Tag! Könnten Sie mir bitte Ihre Kundennummer mitteilen?

                                    ===INTERNAL_INSTRUCTIONS===
                                    - Prüfen Sie die genannte Kundennummer EXAKT wie vom Kunden angegeben mit getCustomer
                                    - Fügen Sie KEINE zusätzlichen Ziffern hinzu
                                    - Sprechen Sie die Kundennummer zur Bestätigung einzeln aus (z.B. "vier-zwei-drei")
                                    - Warten Sie auf Bestätigung vom Kunden, dass die Nummer korrekt ist
                                    - Bei Fehler oder Verneinung: Höflich um erneute Nennung der kompletten Nummer bitten
                                    - Bei erfolgreicher Prüfung und Bestätigung: Erst dann changeStage zu customer_service
                                    - Bleiben Sie stets höflich und professionell""",
                                "customer_context": result,
                                "toolResultText": "Kunde verifiziert"
                            }
                        }
                        response["result"] = json.dumps(stage_response, cls=FirebaseEncoder)
                    else:
                        logging.info("Customer not found")
                        response["result"] = json.dumps({
                            "success": False,
                            "message": "Entschuldigung, ich konnte diese Kundennummer leider nicht finden. Könnten Sie sie bitte noch einmal überprüfen?"
                        })
                except Exception as e:
                    logging.error(f"Error in getCustomer: {str(e)}")
                    response["result"] = json.dumps({
                        "success": False,
                        "message": "Entschuldigung, bei der Kundensuche ist ein Fehler aufgetreten. Können Sie die Nummer bitte noch einmal nennen?"
                    })
                
            elif tool_name == "getAllCustomers":
                result = db_tools.get_all_customers()
                response["result"] = json.dumps(result, cls=FirebaseEncoder)
                
            elif tool_name == "updateCustomer":
                success = db_tools.update_customer(
                    parameters["customerId"],
                    json.loads(parameters["data"])
                )
                response["result"] = json.dumps({"success": success}, cls=FirebaseEncoder)

            elif tool_name == "updateAbschlag":
                try:
                    # Get the customer data from the parameters
                    customer_data = parameters.get("customer_data", {})
                    if not customer_data:
                        response["result"] = json.dumps({
                            "success": False,
                            "message": "Keine Kundendaten verfügbar."
                        })
                        return
                        
                    kundennummer = customer_data.get("kundennummer", "")
                    logging.info(f"Working with customer: {kundennummer}")
                    
                    # Get the amount directly from the parameters
                    new_amount = float(parameters.get("new_amount", 0))
                    logging.info(f"Amount from parameters: {new_amount}")
                    
                    # Convert to integer if it's a whole number
                    new_amount = int(new_amount) if new_amount.is_integer() else new_amount
                    
                    if new_amount <= 0:
                        response["result"] = json.dumps({
                            "success": False,
                            "message": "Der Abschlagsbetrag muss größer als 0 sein."
                        })
                        return
                        
                    # Create the update data with the correct structure
                    update_data = {
                        "abschlag": {
                            "betrag": new_amount,
                            "zahlungsrhythmus": customer_data.get("abschlag", {}).get("zahlungsrhythmus", "monatlich"),
                            "naechsteFaelligkeit": customer_data.get("abschlag", {}).get("naechsteFaelligkeit", 
                                datetime.datetime.now().isoformat())
                        }
                    }
                    
                    # Update the customer record using the current customer's data
                    success = db_tools.update_customer(kundennummer, update_data)
                    
                    if success:
                        response["result"] = json.dumps({
                            "success": True,
                            "message": f"Der Abschlag wurde erfolgreich auf {new_amount} Euro aktualisiert."
                        }, cls=FirebaseEncoder)
                    else:
                        response["result"] = json.dumps({
                            "success": False,
                            "message": "Die Aktualisierung des Abschlags ist fehlgeschlagen."
                        })
                except ValueError as e:
                    logging.error(f"Error parsing amount: {str(e)}")
                    response["result"] = json.dumps({
                        "success": False,
                        "message": "Der angegebene Betrag konnte nicht als Zahl erkannt werden."
                    })
                except Exception as e:
                    logging.error(f"Error in updateAbschlag: {str(e)}")
                    response["result"] = json.dumps({
                        "success": False,
                        "message": "Ein Fehler ist aufgetreten: " + str(e)
                    })

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
                
            elif tool_name == "changeStage":
                try:
                    stage = parameters.get("stage")
                    customer_data = parameters.get("customer_data", {})
                    
                    # Get the appropriate prompt and format it with customer data if available
                    prompt = STAGE_PROMPTS.get(stage, args.system_prompt)
                    if customer_data:
                        try:
                            name = customer_data.get("name", "dem Kunden")
                            current_amount = customer_data.get("abschlag", {}).get("betrag", "0")
                            prompt = prompt.format(name=name, current_amount=current_amount)
                        except KeyError:
                            # If formatting fails, use the prompt as is
                            pass
                    
                    # Split the prompt into customer-facing and internal parts
                    parts = prompt.split("===INTERNAL_INSTRUCTIONS===")
                    customer_prompt = parts[0].strip()
                    internal_instructions = parts[1].strip() if len(parts) > 1 else ""
                    
                    # Prepare the stage change response with separated prompts
                    stage_response = {
                        "type": "client_tool_result",
                        "responseType": "new-stage",
                        "body": {
                            "systemPrompt": f"{customer_prompt}\n\n{internal_instructions}",
                            "toolResultText": "OK"
                        }
                    }
                    
                    if customer_data:
                        stage_response["body"]["customer_context"] = customer_data
                    
                    response["result"] = json.dumps(stage_response, cls=FirebaseEncoder)
                    
                except Exception as e:
                    logging.error(f"Error in changeStage: {str(e)}")
                    response["errorType"] = "StageChangeError"
                    response["errorMessage"] = f"Failed to change stage: {str(e)}"

            elif tool_name == "endCall":
                try:
                    # Send a simple goodbye message
                    goodbye_response = {
                        "type": "client_tool_result",
                        "responseType": "message",
                        "body": {
                            "message": "Vielen Dank für Ihren Anruf. Auf Wiederhören!"
                        }
                    }
                    response["result"] = json.dumps(goodbye_response)
                    
                    # End the call immediately
                    await self.stop()
                except Exception as e:
                    logging.error(f"Error ending call: {str(e)}")
                    response["result"] = json.dumps({
                        "success": False,
                        "message": "Ein Fehler ist aufgetreten beim Beenden des Gesprächs."
                    })

            else:
                response["errorType"] = "undefined"
                response["errorMessage"] = f"Unknown tool: {tool_name}"
                
        except Exception as e:
            response["errorType"] = type(e).__name__
            response["errorMessage"] = str(e)
            
        await self._socket.send(json.dumps(response))

    async def _pump_audio(self, source: LocalAudioSource):
        """Pump audio data with connection state checking."""
        while self._running:
            try:
                async for chunk in source.stream():
                    if not self._running:
                        break
                    if not self._socket or not self._socket.open:
                        logging.warning("Socket not connected, buffering audio...")
                        await asyncio.sleep(0.1)
                        continue
                    try:
                        await self._socket.send(chunk)
                    except ws_exceptions.ConnectionClosed:
                        logging.warning("Connection closed while sending audio")
                        break
                    except Exception as e:
                        logging.error(f"Error sending audio chunk: {e}")
                        break
            except Exception as e:
                if self._running:
                    logging.error(f"Error in audio pump: {e}")
                    await asyncio.sleep(1)  # Wait before retrying
                else:
                    break


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
                    "modelToolName": "changeStage",
                    "description": "Changes the conversation stage. Used for transitioning between different phases of the conversation.",
                    "client": {},
                    "dynamicParameters": [
                        {
                            "name": "stage",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {
                                "type": "string",
                                "description": "The stage to transition to (authentication, customer_service, abschlag_management)",
                                "enum": ["authentication", "customer_service", "abschlag_management"]
                            },
                            "required": True
                        },
                        {
                            "name": "customer_data",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {
                                "type": "object",
                                "description": "Customer data to carry forward to next stage"
                            },
                            "required": False
                        }
                    ]
                }
            },
            {
                "temporaryTool": {
                    "modelToolName": "getCustomer",
                    "description": "Sucht einen Kunden anhand seiner Kundennummer",
                    "client": {},
                    "dynamicParameters": [
                        {
                            "name": "kundennummer",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {
                                "type": "string",
                                "description": "Die Kundennummer des Kunden"
                            },
                            "required": True
                        }
                    ]
                },
            },
            {
                "temporaryTool": {
                    "modelToolName": "updateAbschlag",
                    "description": "Aktualisiert den Abschlag für einen Kunden. Beispiel: 'Ich würde gerne meinen Abschlag auf dreiundsechzig Euro ändern'",
                    "client": {},
                    "dynamicParameters": [
                        {
                            "name": "new_amount",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {
                                "type": "number",
                                "description": "Der neue Abschlagsbetrag in Euro (z.B. 34 für vierunddreißig Euro)"
                            },
                            "required": True
                        },
                        {
                            "name": "customer_data",
                            "location": "PARAMETER_LOCATION_BODY",
                            "schema": {
                                "type": "object",
                                "description": "Die Kundendaten des aktuellen Kunden"
                            },
                            "required": True
                        }
                    ]
                }
            },
            {
                "temporaryTool": {
                    "modelToolName": "endCall",
                    "description": "Beendet das Gespräch nach erfolgreicher Bearbeitung des Anliegens",
                    "client": {},
                }
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


def _convert_to_german_number_words(number: int) -> str:
    """Convert a number to German words."""
    units = ["", "ein", "zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun"]
    teens = ["zehn", "elf", "zwölf", "dreizehn", "vierzehn", "fünfzehn", "sechzehn", "siebzehn", "achtzehn", "neunzehn"]
    tens = ["", "", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig", "siebzig", "achtzig", "neunzig"]
    
    if number < 0:
        return f"minus {_convert_to_german_number_words(abs(number))}"
    if number == 0:
        return "null"
    if number < 10:
        return units[number]
    if number < 20:
        return teens[number - 10]
    if number < 100:
        unit = number % 10
        ten = number // 10
        if unit == 0:
            return tens[ten]
        return f"{units[unit]}und{tens[ten]}"
    if number < 1000:
        hundreds = number // 100
        rest = number % 100
        if rest == 0:
            return f"{units[hundreds]}hundert"
        return f"{units[hundreds]}hundert{_convert_to_german_number_words(rest)}"
    return str(number)  # For larger numbers, return as is


async def main():
    join_url = await _get_join_url()
    client = WebsocketVoiceSession(join_url)
    
    db_tools = DatabaseTools()
    done = asyncio.Event()
    loop = asyncio.get_running_loop()

    @client.on("state")
    async def on_state(state):
        if state == "listening":
            print("\nUser:  ", end="", flush=True)
        elif state == "thinking":
            print("\nAgent: ", end="", flush=True)

    @client.on("output")
    async def on_output(text, final):
        display_text = f"{text.strip()}"
        if final:
            print(display_text)
        else:
            print(display_text, end="\r", flush=True)
        if final:
            # Save the conversation
            conversation_data = {
                "timestamp": datetime.datetime.now(),
                "text": text.strip(),
                "type": "agent"
            }
            db_tools.save_conversation(conversation_data, is_ultravox=True)

    @client.on("input")
    async def on_input(text, final):
        if final:
            # Save the user input
            conversation_data = {
                "timestamp": datetime.datetime.now(),
                "text": text.strip(),
                "type": "user"
            }
            db_tools.save_conversation(conversation_data, is_ultravox=True)

    @client.on("error")
    async def on_error(error):
        logging.exception("Client error", exc_info=error)
        print(f"Error: {error}")
        done.set()

    @client.on("ended")
    async def on_ended():
        try:
            print("\nSession ended.")  # Add newline and period
            logging.info("Session ended normally")
        except Exception as e:
            logging.error(f"Error during session end: {e}")
        finally:
            done.set()

    try:
        # Try to add signal handlers for Unix systems
        loop.add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(handle_shutdown()))
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(handle_shutdown()))
    except (NotImplementedError, AttributeError):
        # On Windows, we'll handle keyboard interrupt directly
        def windows_signal_handler(signum, frame):
            asyncio.create_task(handle_shutdown())
        signal.signal(signal.SIGINT, windows_signal_handler)

    async def handle_shutdown():
        """Handle graceful shutdown of the application."""
        try:
            print("\nShutting down...")
            logging.info("Starting graceful shutdown")
            done.set()
            await client.stop()
        except Exception as e:
            logging.error(f"Error during shutdown: {e}")

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
        default=STAGE_PROMPTS["authentication"],

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
    parser.add_argument(
        "--mic-threshold",
        type=float,
        default=0.01,
        help="Microphone sensitivity threshold (0.0 to 1.0, default: 0.01)",
    )

    args = parser.parse_args()
    if args.very_verbose:
        logging.basicConfig(
            stream=sys.stdout,
            level=logging.DEBUG,
            format='%(levelname)s: %(message)s'
        )
    elif args.verbose:
        logging.basicConfig(
            stream=sys.stdout,
            level=logging.INFO,
            format='%(levelname)s: %(message)s'
        )
    else:
        logging.basicConfig(
            stream=sys.stdout,
            level=logging.WARNING,
            format='%(levelname)s: %(message)s'
        )

    # Remove binary debug logging
    logging.getLogger('websockets').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Application terminated by user")
    except Exception as e:
        logging.error(f"Application error: {e}", exc_info=True)
