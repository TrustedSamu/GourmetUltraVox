@echo off
echo Checking audio devices...
python -c "import sounddevice; print('\nInput devices:'); print(sounddevice.query_devices(kind='input')); print('\nOutput devices:'); print(sounddevice.query_devices(kind='output')); print('\nDefault devices:'); print('Default input:', sounddevice.query_devices(None, 'input')['name']); print('Default output:', sounddevice.query_devices(None, 'output')['name'])"

if errorlevel 1 (
    echo Error: Could not initialize audio devices
    pause
    exit /b 1
)

set ULTRAVOX_API_KEY=GROJVSKu.L1eJ5U9ORGjs775iZfR1iNYSY3mqch9T
call .venv\Scripts\activate.bat

echo Starting websocket client with verbose logging...
python websocket_client.py --voice 7973800a-ebd5-4756-a211-d609c66e1b8f --very-verbose --mic-threshold 0.3
if errorlevel 1 (
    echo Error: Application crashed. Check the logs above for details.
    pause
    exit /b 1
)
pause 