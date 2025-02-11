# UltraVox Energy Services Assistant

An AI-powered call center solution designed specifically for energy and utility service providers. This system uses advanced voice recognition and natural language processing to handle customer inquiries, service requests, and support issues efficiently.

## Features

- Real-time voice interaction with customers
- Natural language processing for understanding customer inquiries
- Handles common utility service requests:
  - Billing inquiries
  - Service outage reporting
  - Account management
  - Payment arrangements
  - Service scheduling
  - Emergency response coordination

## Technical Overview

The system uses WebSocket-based communication for real-time voice processing and integrates with Ultravox's advanced AI capabilities for natural conversation handling.

### Components

- WebSocket Client: Handles real-time audio streaming and voice processing
- Web Interface: Modern dashboard for call monitoring and management
- Voice Processing: High-quality text-to-speech and speech-to-text conversion
- State Management: Tracks conversation flow and customer interactions

## Setup

1. Install Python 3.8+ and dependencies:
```bash
uv pip install -r requirements.txt
```

2. Set up your environment variables:
```bash
export ULTRAVOX_API_KEY='your_api_key_here'
```

3. Start the web interface:
```bash
python web_app.py
```

4. Launch the WebSocket client:
```bash
python websocket_client.py
```

## Security

- All sensitive data is handled according to industry standards
- API keys and credentials are managed securely through environment variables
- Communication is encrypted using secure WebSocket protocols

## Usage

The system can handle multiple concurrent calls and automatically routes customers to appropriate service queues based on their inquiries. The AI assistant can:

1. Authenticate customers
2. Access account information
3. Process service requests
4. Handle emergency situations
5. Transfer to human agents when necessary

## Contributing

Contributions are welcome! Please read our contributing guidelines before submitting pull requests.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
