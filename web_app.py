from flask import Flask, render_template, jsonify
import os
import subprocess
import signal
import sys
import atexit

app = Flask(__name__)
websocket_process = None
current_state = "idle"

def cleanup_process():
    global websocket_process
    if websocket_process and websocket_process.poll() is None:
        try:
            websocket_process.terminate()
            websocket_process.wait(timeout=5)  # Wait up to 5 seconds for graceful shutdown
        except subprocess.TimeoutExpired:
            websocket_process.kill()  # Force kill if it doesn't terminate
        websocket_process = None

atexit.register(cleanup_process)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start')
def start_session():
    global websocket_process
    if not os.getenv('ULTRAVOX_API_KEY'):
        return jsonify({'error': 'ULTRAVOX_API_KEY environment variable not set'}), 400

    if websocket_process is None or websocket_process.poll() is not None:
        # Clean up any existing process
        cleanup_process()
        
        # Set environment variable for Flask state callback
        env = os.environ.copy()
        env['FLASK_STATE_CALLBACK'] = 'true'
        
        # Start the websocket client in a new process
        try:
            websocket_process = subprocess.Popen(
                [sys.executable, 'websocket_client.py'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                env=env
            )
            return jsonify({'status': 'started'})
        except Exception as e:
            return jsonify({'error': f'Failed to start process: {str(e)}'}), 500
    else:
        return jsonify({'status': 'already_running'})

@app.route('/stop')
def stop_session():
    global websocket_process
    if websocket_process and websocket_process.poll() is None:
        cleanup_process()
        return jsonify({'status': 'stopped'})
    return jsonify({'status': 'not_running'})

@app.route('/update_state/<state>')
def update_state(state):
    global current_state
    current_state = state
    return jsonify({'status': 'ok'})

@app.route('/get_state')
def get_state():
    return jsonify({'state': current_state})

if __name__ == '__main__':
    app.run(debug=True)
