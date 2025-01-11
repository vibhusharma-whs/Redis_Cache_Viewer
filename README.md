# Redis Cache Viewer

A modern, user-friendly desktop application built with Python and Dash for viewing and managing Redis cache entries. This tool provides a clean interface for inspecting Redis keys, their values, and metadata, with support for various serialization formats and compression algorithms.

![Redis Cache Viewer Screenshot](screenshot.png)

## Features

- **Real-time Cache Inspection**: View all Redis cache keys in a sortable, filterable table
- **Advanced Value Decoding**: Support for multiple serialization formats:
  - GOB (Go Binary Format)
  - MessagePack
  - JSON
  - Go JSON
- **Compression Support**: Handles various compression algorithms:
  - Gzip
  - Snappy
  - LZ4
  - Uncompressed data
- **Key Metadata**: Display important information for each key:
  - TTL (Time To Live)
  - Size in KB
  - Serialization format
- **Search Functionality**: Filter keys using pattern matching
- **JSON Viewer**: Syntax-highlighted JSON preview with copy functionality
- **Cache Management**: Ability to clear individual cache entries

## Prerequisites

- Python 3.x
- Redis Server
- macOS (for the current launcher configuration)

## Installation

1. Clone the repository:

```bash
git clone https://github.com/yourusername/redis-cache-viewer.git
cd redis-cache-viewer
```

2. Install required dependencies:

```bash
pip install -r requirements.txt
```

3. Configure Redis connection:
Create a `local.env` file with your Redis credentials:

```env
REDIS_HOST=127.0.0.1
REDIS_PORT=63791
REDIS_DB=10
REDIS_PASSWORD=your_password
```

4. Set up the launch agent (macOS):
```bash
cp com.redisviewer.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.redisviewer.plist
```

## Usage

1. Start the application:
```bash
python redis_stream.py
```

2. The application will automatically open in your default browser at `http://127.0.0.1:8050`

3. Use the search bar to filter keys by pattern
4. Click on any key to view its decoded value
5. Use the "Copy JSON" button to copy the decoded value to clipboard
6. Use the "Clear Cache" button to remove individual keys

## Architecture

### Components

- **RedisInstance**: Handles Redis connection and basic operations
- **RedisCacheViewer**: Core class managing Redis interactions and data processing
- **Dash Application**: Web interface built with Dash and AG Grid
- **Launch Agent**: macOS service configuration for automatic startup

### Serialization Support

The application automatically detects and handles different serialization formats based on key prefixes:
- `s2`: MessagePack
- `s3`: JSON
- `s4`: Go JSON
- Default: GOB

### Compression Detection

Compression algorithm is determined by key prefixes:
- `c0`: No compression
- `c1`: Gzip
- `c2`: Snappy
- `c3`: LZ4

## Development

### Project Structure
```
redis-cache-viewer/
├── redis_stream.py      # Main application file
├── local.env           # Redis configuration
├── requirements.txt    # Python dependencies
├── com.redisviewer.plist # Launch agent configuration
└── logs/              # Application logs
    ├── error.log
    └── output.log
```

### Logging

The application logs are stored in:
- `logs/error.log`: Error and debug information
- `logs/output.log`: Standard output logs

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [Dash](https://dash.plotly.com/) for the web framework
- [AG Grid](https://www.ag-grid.com/) for the powerful data grid
- [Redis](https://redis.io/) for the in-memory data store

## Support

For support, please open an issue in the GitHub repository or contact [your-email@example.com](mailto:your-email@example.com).
