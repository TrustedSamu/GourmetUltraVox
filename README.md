# Building an AI Voice Assistant with Ultravox 🎙️

## 🎥 Watch DeepDive Video

Watch the DeepDive Crash Course on Our YouTube Channel:

<p align="center">
    <a href="https://www.youtube.com/watch?v=tgPR-ZBqOGY">
        <img src="https://img.youtube.com/vi/tgPR-ZBqOGY/0.jpg" alt="Ultravox AI Voice Assistant Tutorial" width="560" height="315">
    </a>
</p>

<p align="center">
    <a href="https://www.youtube.com/channel/UCxgkN3luQgLQOd_L7tbOdhQ?sub_confirmation=1">
        <img src="https://img.shields.io/badge/Subscribe-FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="Subscribe">
    </a>
</p>

## 🚀 Introduction

This project demonstrates how to build an engaging AI voice assistant using Ultravox's WebSocket API. The assistant takes the form of a fun donut shop drive-thru operator, complete with:
- Real-time voice interactions
- Beautiful animated UI
- Natural language understanding
- Dynamic state management
- Interactive menu handling

## 🛠️ Key Features

- **Voice Interface**: Real-time voice input/output using WebSocket streaming
- **Interactive UI**: Animated donut drive-thru theme with microphone button
- **State Management**: Visual feedback for listening, thinking, and speaking states
- **Menu System**: Complete donut shop menu with prices
- **Error Handling**: Graceful handling of connection and state issues

## 🔧 Getting Started

1. Clone this repository:
```bash
git clone https://github.com/yourusername/ultravox-websocket-example-client.git
cd ultravox-websocket-example-client
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up your Ultravox API key:
```bash
export ULTRAVOX_API_KEY='your_api_key_here'
```

4. Run the application:
```bash
python web_app.py
```

5. Open your browser and navigate to:
```
http://localhost:5000
```

## 📚 Project Structure

- `web_app.py`: Flask server for web interface
- `websocket_client.py`: Core WebSocket client for voice interactions
- `templates/index.html`: Interactive UI with animations
- `requirements.txt`: Project dependencies

## 🎯 Core Components

### Voice Client
- Handles real-time audio streaming
- Manages WebSocket connection
- Processes voice state changes
- Handles transcripts and responses

### Web Interface
- Beautiful donut drive-thru themed UI
- Animated microphone button
- Visual state feedback
- Error handling and status messages

### Menu System
- Complete donut and beverage menu
- Price tracking
- Order management
- Secret menu feature

## 🔑 Requirements

- Python 3.7+
- Ultravox API key
- Modern web browser
- Microphone access
- Speaker/audio output

## 🎮 Usage

1. Click the microphone button to start
2. Speak your order naturally
3. The AI will respond as a friendly drive-thru operator
4. Order donuts, coffee, or ask about specials
5. The UI will animate based on the conversation state

## 🛡️ Error Handling

The system handles various scenarios:
- Connection issues
- Audio device problems
- Invalid API keys
- Unexpected states

## 🤝 Contributing

We welcome contributions! Please feel free to submit a Pull Request.

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

### Community and Support
- Join our community: [Kno2gether Community](https://community.kno2gether.com)
- Full Production Ready SaaS Launch Course (50% OFF): [End-to-End SaaS Launch Course](https://knolabs.biz/course-at-discount)

### Hosting Partners
- [Kamatera - Get $100 Free VPS Credit](https://knolabs.biz/100-dollar-free-credit)
- [Hostinger - Additional 20% Discount](https://knolabs.biz/20-Percent-Off-VPS)

## 📺 Video Tutorials

Follow along with our detailed video tutorials on the [Kno2gether YouTube Channel](https://youtube.com/@kno2gether) for step-by-step guidance and best practices.

## 🔗 Additional Resources

- [Ultravox Documentation](https://docs.ultravox.ai)
- [WebSocket API Reference](https://docs.ultravox.ai/websocket-api)
- [Example Projects](https://github.com/ultravox-ai/examples)

## 🎓 Advanced Topics

### Custom Voice Configuration
```python
# Configure voice settings
voice_config = {
    "voice": "custom_voice_id",
    "temperature": 0.8,
    "experimental_messages": "debug"
}
```

### State Management
```python
# Handle voice states
@client.on("state")
async def on_state(state):
    match state:
        case "listening":
            # Update UI for listening state
        case "thinking":
            # Show thinking animation
        case "speaking":
            # Animate for speaking state
```

### Menu Customization
```python
# Add custom menu items
menu_items = {
    "donuts": [
        {"name": "Pumpkin Spice", "price": 1.29},
        {"name": "Old Fashioned", "price": 1.29}
    ],
    "drinks": [
        {"name": "Regular Coffee", "price": 1.79},
        {"name": "Latte", "price": 3.49}
    ]
}
```

## 🎉 Conclusion

This project showcases how to build an engaging voice assistant using Ultravox's WebSocket API. The donut drive-thru theme makes it fun and interactive while demonstrating key voice AI concepts.

Happy coding! 🚀
