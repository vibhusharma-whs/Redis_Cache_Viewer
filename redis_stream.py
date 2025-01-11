import dash
from dash import html, dcc, Input, Output, State
import dash_ag_grid as dag
import redis, json, msgpack, gzip, snappy, lz4.block
from io import BytesIO
from enum import Enum
from flask import request, jsonify
from typing import Any, Optional, Dict, List
from dataclasses import dataclass
import logging, pickle, pandas as pd, html as html_lib
from datetime import datetime
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import JsonLexer
import webbrowser, threading, time


# Enums and basic classes remain the same
class SerializationType(Enum):
    GOB = "gob"
    MSG_PACK = "msgpack"
    JSON = "json"
    GO_JSON = "gojson"


class CompressionAlgorithm(Enum):
    NONE = "none"
    ZIP = "zip"
    SNAPPY = "snappy"
    LZ4 = "lz4"


class CacheError(Exception):
    def __init__(self, description: str):
        self.description = description
        super().__init__(description)


@dataclass
class RedisInstance:
    host: str
    port: int
    db: int
    password: str = ""
    client: Optional[redis.Redis] = None

    def connect(self) -> None:
        if not self.client:
            self.client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                password=self.password,
                decode_responses=False,
            )

    def get_string(self, key: str) -> Optional[bytes]:
        self.connect()
        return self.client.get(key)


class RedisCacheViewer:
    def __init__(self):
        self.redis_instance = None
        self.setup_logging()
        # Auto-connect on initialization
        self.connect_to_redis(
            host="127.0.0.1", port=63791, db=10, password="6CW5nNsoa1XphJxfi4xw"
        )

    def setup_logging(self):
        logging.basicConfig(level=logging.ERROR)
        self.logger = logging.getLogger("CacheLogger")

    def connect_to_redis(self, host: str, port: int, db: int, password: str) -> bool:
        try:
            self.redis_instance = RedisInstance(
                host=host, port=port, db=db, password=password
            )
            self.redis_instance.connect()
            self.redis_instance.client.ping()
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Redis: {str(e)}")
            return False

    def get_keys(self, pattern: str = "*") -> List[str]:
        if not self.redis_instance or not self.redis_instance.client:
            self.logger.error("Redis instance or client is None")
            return []
        try:
            keys = []
            cursor = 0
            while True:
                # Redis internally scans ~5000 keys
                # Applies pattern matching to ALL scanned keys
                # Returns only the matches found within those ~5000 keys
                cursor, partial_keys = self.redis_instance.client.scan(
                    cursor=cursor, match=pattern, count=5000
                )
                # Continue until cursor = 0 (meaning we've scanned ALL keys in the database)
                keys.extend(
                    [
                        key.decode("utf-8") if isinstance(key, bytes) else key
                        for key in partial_keys
                    ]
                )
                if cursor == 0:
                    break
            return sorted(keys)
        except Exception as e:
            self.logger.error(f"Failed to fetch keys: {str(e)}")
            return []

    def get_ttl(self, key: str) -> str:
        try:
            ttl_seconds = self.redis_instance.client.ttl(key)
            if ttl_seconds > -1:
                ttl_minutes = ttl_seconds / 60
                if ttl_minutes >= 1:
                    return f"{int(ttl_minutes)} min"
                return "< 1 min"
            return "No TTL"
        except Exception as e:
            return f"Error: {str(e)}"

    def get_value(self, key: str) -> Optional[Dict]:
        try:
            compression_algorithm = get_compression_algorithm(key)
            serialization_type = get_serialization_type(key)

            data = self.redis_instance.get_string(key)
            if not data:
                return None

            result = {}
            error = decode(data, result, compression_algorithm, serialization_type)
            if error:
                return {"error": str(error)}
            return result
        except Exception as e:
            return {"error": str(e)}

    def get_object_size(self, key: str) -> Optional[int]:
        """Returns the size of the cached object in bytes."""
        try:
            data = self.redis_instance.get_string(key)
            if data is not None:
                return len(data)
            return None
        except Exception as e:
            self.logger.error(f"Failed to get object size: {str(e)}")
            return None

    def check_redis_status(self) -> Dict[str, Any]:
        try:
            info = self.redis_instance.client.info()
            keyspace = {k: v for k, v in info.items() if k.startswith("db")}
            db_info = info.get(f"db{self.redis_instance.db}", {})
            config_databases = self.redis_instance.client.config_get("databases")

            return {
                "connected": True,
                "total_keys": db_info.get("keys", 0),
                "info": db_info,
                "all_databases": keyspace,
            }
        except Exception as e:
            self.logger.error(f"Failed to get Redis status: {str(e)}")
            return {"connected": False, "error": str(e)}


def get_compression_algorithm(cache_key: str) -> CompressionAlgorithm:
    if len(cache_key) > 2:
        prefix = cache_key[:2]
        if prefix == "c1":
            return CompressionAlgorithm.ZIP
        elif prefix == "c2":
            return CompressionAlgorithm.SNAPPY
        elif prefix == "c3":
            return CompressionAlgorithm.LZ4
        elif prefix == "c0":
            return CompressionAlgorithm.NONE
    return CompressionAlgorithm.ZIP


def get_serialization_type(cache_key: str) -> SerializationType:
    if len(cache_key) > 4:
        parts = cache_key.split(".", 1)
        if len(parts) > 1 and len(parts[1]) > 1:
            prefix = parts[1][:2]
            if prefix == "s2":
                return SerializationType.MSG_PACK
            elif prefix == "s3":
                return SerializationType.JSON
            elif prefix == "s4":
                return SerializationType.GO_JSON
    return SerializationType.GOB


def decode(
    data: bytes,
    obj: Any,
    compression_algorithm: CompressionAlgorithm,
    serialization_type: SerializationType,
) -> Optional[Exception]:
    try:
        # Decompress first
        try:
            if compression_algorithm == CompressionAlgorithm.SNAPPY:
                decompressed = snappy.decompress(data)
            elif compression_algorithm == CompressionAlgorithm.ZIP:
                buffer = BytesIO(data)
                with gzip.GzipFile(fileobj=buffer, mode="rb") as gz:
                    decompressed = gz.read()
            elif compression_algorithm == CompressionAlgorithm.LZ4:
                decompressed = lz4.block.decompress(data)
            else:  # NONE
                decompressed = data
        except Exception as decompress_error:
            raise CacheError(f"Decompression failed: {str(decompress_error)}")

        # Deserialize
        try:
            if serialization_type == SerializationType.MSG_PACK:
                result = msgpack.unpackb(decompressed, raw=False)
            elif serialization_type == SerializationType.GOB:
                result = pickle.loads(decompressed)
            elif serialization_type in (
                SerializationType.JSON,
                SerializationType.GO_JSON,
            ):
                result = json.loads(decompressed.decode("utf-8"))

            if isinstance(obj, dict):
                obj.clear()
                obj.update(result if isinstance(result, dict) else {"value": result})
            else:
                for key, value in (
                    result if isinstance(result, dict) else {"value": result}
                ).items():
                    setattr(obj, key, value)
            return None

        except Exception as deserialize_error:
            raise CacheError(f"Deserialization failed: {str(deserialize_error)}")

    except Exception as e:
        return e


# Initialize the Dash app
app = dash.Dash(__name__, suppress_callback_exceptions=True)
cache_viewer = RedisCacheViewer()

# Add Pygments CSS
app.index_string = """
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
            .json-key { color: #f92672; }
            .json-string { color: #a6e22e; }
            .json-number { color: #ae81ff; }
            .json-boolean { color: #66d9ef; }
            .json-null { color: #fd971f; }
            .dash-debug-menu__outer, .dash-debug-menu {
                display: none !important;
            }
            .refresh-button {
                background-color: transparent;
                border: none;
                color: #a6a6a6;
                cursor: pointer;
                font-size: 20px;
                padding: 5px 10px;
                margin-right: 10px;
                margin-top: 15px;
                vertical-align: middle;
                display: inline-flex;
                align-items: center;
                transition: all 0.3s ease;
            }
            .refresh-button:hover {
                color: #ffffff;
            }
            .refresh-button:active {
                color: #ffffff;
                transform: rotate(180deg);
            }
            .refresh-button.rotating {
                color: #ffffff;
                transform: rotate(180deg);
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
"""

# Update the layout - remove the Style component
app.layout = html.Div(
    [
        dcc.Store(id="refresh-trigger", data=0),
        html.H1(
            "REDIS CACHE VIEWER",
            style={
                "textAlign": "left",
                "marginBottom": "20px",
                "fontSize": "28px",
                "fontFamily": "'Inter', 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif",
                "fontWeight": "600",
                "letterSpacing": "1px",
                "textTransform": "uppercase",
                "color": "#ffffff",
            },
        ),
        html.Link(
            rel="stylesheet",
            href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css",
        ),
        dcc.Input(
            id="search-pattern",
            type="text",
            placeholder="Search Pattern (e.g., *)",
            value="",
            style={
                "width": "calc(40% - 60px)",
                "marginBottom": "20px",
                "backgroundColor": "#3d3d3d",
                "color": "#ffffff",
                "border": "1px solid #4d4d4d",
                "borderRadius": "10px",
                "height": "40px",
                "padding": "0 20px",
                "fontSize": "14px",
                "outline": "none",
                "transition": "border-color 0.3s ease",
            },
        ),
        html.Div(
            [
                # Left column - Keys Table with metadata
                html.Div(
                    [
                        html.Div(
                            [  # Wrapper div for header row
                                html.Div(
                                    [  # Left side - CACHE KEYS and refresh button
                                        html.H3(
                                            "CACHE KEYS",
                                            style={
                                                "fontSize": "22px",
                                                "fontFamily": "'Inter', 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif",
                                                "fontWeight": "600",
                                                "letterSpacing": "1px",
                                                "textTransform": "uppercase",
                                                "color": "#ffffff",
                                                "marginBottom": "5px",
                                                "display": "inline-block",
                                                "marginRight": "5px",
                                            },
                                        ),
                                        html.Button(
                                            html.I(className="fas fa-sync-alt"),
                                            id="refresh-button",
                                            className="refresh-button",
                                            n_clicks=0,
                                        ),
                                    ],
                                    style={
                                        "display": "flex",
                                        "alignItems": "center",
                                    },
                                ),
                                # Right side - Total Keys
                                html.H1(
                                    id="total-keys",
                                    style={
                                        "color": "#a6a6a6",
                                        "fontSize": "14px",
                                        "fontFamily": "'Inter', sans-serif",
                                        "marginBottom": "5px",
                                        "lineHeight": "22px",
                                        "paddingTop": "15px",
                                    },
                                ),
                            ],
                            style={
                                "display": "flex",
                                "alignItems": "center",
                                "justifyContent": "space-between",  # This spreads the elements
                                "width": "100%",  # Ensure full width
                                "marginBottom": "10px",
                            },
                        ),
                        dag.AgGrid(
                            id="keys-table",
                            rowData=[],
                            columnDefs=[
                                {"field": "key", "headerName": "Key", "flex": 2},
                                {"field": "ttl", "headerName": "TTL", "flex": 1},
                                {"field": "size", "headerName": "Size (KB)", "flex": 1},
                                {
                                    "field": "serialization",
                                    "headerName": "Serialization",
                                    "flex": 1,
                                },
                            ],
                            defaultColDef={
                                "resizable": True,
                                "sortable": True,
                                "filter": True,
                            },
                            dashGridOptions={
                                "rowSelection": "single",
                                "headerClass": "custom-header",
                            },
                            style={
                                "height": "calc(100vh - 250px)",
                                "width": "100%",
                                "borderRadius": "8px",
                                "--ag-header-background-color": "#654321",
                                "--ag-header-foreground-color": "#ffffff",
                                "--ag-header-height": "45px",
                                "--ag-border-radius": "8px",
                                "--ag-borders": "solid 1px",
                                "--ag-border-color": "#4d4d4d",
                            },
                            className="ag-theme-alpine-dark",
                        ),
                    ],
                    style={
                        "width": "40%",
                        "display": "inline-block",
                        "verticalAlign": "top",
                        "paddingRight": "20px",
                        "height": "calc(100vh - 190px)",
                    },
                ),
                # Right column - JSON Preview
                html.Div(
                    [
                        html.Div(
                            [
                                html.H3(
                                    "DECODED VALUE",
                                    style={
                                        "fontSize": "22px",
                                        "fontFamily": "'Inter', 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif",
                                        "fontWeight": "600",
                                        "letterSpacing": "1px",
                                        "textTransform": "uppercase",
                                        "color": "#ffffff",
                                        "marginBottom": "15px",
                                        "display": "inline-block",
                                    },
                                ),
                            ],
                            style={
                                "display": "flex",
                                "justifyContent": "space-between",
                                "alignItems": "center",
                            },
                        ),
                        html.Div(
                            id="json-preview",
                            style={
                                "height": "calc(100vh - 295px)",
                                "overflowY": "auto",
                                "backgroundColor": "#272822",
                                "padding": "20px",
                                "borderRadius": "8px",
                                "boxShadow": "0 4px 6px rgba(0, 0, 0, 0.1)",
                                "color": "#ffffff",
                            },
                        ),
                    ],
                    style={
                        "width": "60%",
                        "display": "inline-block",
                        "verticalAlign": "top",
                        "height": "calc(100vh - 240px)",
                    },
                ),
            ],
            style={
                "display": "flex",
                "flexDirection": "row",
                "gap": "20px",
                "height": "calc(100vh - 190px)",
            },
        ),
    ],
    style={
        "padding": "20px",
        "height": "100vh",
        "backgroundColor": "#2d2d2d",
        "overflow": "hidden",
    },
)


@app.callback(
    Output("keys-table", "rowData"),
    [
        Input("search-pattern", "value"),
        Input("refresh-trigger", "data"),
        Input("refresh-button", "n_clicks"),
    ],
)
def update_keys_table(pattern, _, n_clicks):
    # First get all keys
    all_keys = cache_viewer.get_keys("*")
    row_data = []

    # Convert pattern to lowercase for case-insensitive matching
    pattern = pattern.lower() if pattern else "*"

    for key in all_keys:
        # Extract the display key (part after "go.")
        display_key = key.split("go.", 1)[1] if "go." in key else key

        # Only add to row_data if the display_key matches the pattern
        if pattern == "*" or pattern.lower() in display_key.lower():
            size_in_kb = None
            try:
                size_in_bytes = cache_viewer.get_object_size(key)
                if size_in_bytes is not None:
                    size_in_kb = round(size_in_bytes / 1024, 2)  # Convert to KB
            except Exception as e:
                size_in_kb = "Error"

            row_data.append(
                {
                    "key": display_key,  # Display shortened key
                    "original_key": key,  # Keep original key for value lookup
                    "ttl": cache_viewer.get_ttl(key),
                    "size": size_in_kb,
                    "serialization": get_serialization_type(key).value,
                }
            )

    # Sort the filtered results by display key
    row_data.sort(key=lambda x: x["key"].lower())
    return row_data


# Update the value preview callback to use the original key
@app.callback(Output("json-preview", "children"), Input("keys-table", "selectedRows"))
def update_value_preview(selected_rows):
    if not selected_rows or len(selected_rows) == 0:
        return html.Div(
            "No key selected",
            style={
                "fontSize": "18px",
                "color": "#75715e",
                "margin": "10px",
                "fontFamily": "monospace",
            },
        )

    # Use the original key for lookup
    selected_key = selected_rows[0]["original_key"]
    value = cache_viewer.get_value(selected_key)

    if value:
        formatted_json = json.dumps(value, indent=2)
        highlighted_json = highlight(
            formatted_json,
            JsonLexer(),
            HtmlFormatter(
                style="monokai",
                noclasses=True,
                prestyles="font-family: 'Monaco', 'Consolas', monospace; font-size: 14px; line-height: 1.5;",
            ),
        )

        html_content = f"""
            <html>
                <body style="margin:0; background-color: #272822;">
                    <div style="position: absolute; top: 10px; right: 10px;">
                        <button id="copyButton" onclick="copyToClipboard(`{html_lib.escape(formatted_json)}`);"
                                style="background-color: #654321; color: white; border: none; 
                                       padding: 8px 15px; border-radius: 4px; cursor: pointer; 
                                       font-size: 14px; position: relative;">
                            Copy JSON
                        </button>
                        <button id="copyButton" onclick="clearCache(`{html_lib.escape(selected_key)}`);"
                                style="background-color: #B21807; color: white; border: none; 
                                       padding: 8px 15px; border-radius: 4px; cursor: pointer; 
                                       font-size: 14px; position: relative;">
                            Clear Cache
                        </button>
                    </div>
                    {highlighted_json}
                    <script>
                        function copyToClipboard(text) {{
                            navigator.clipboard.writeText(text)
                                .then(() => {{
                                    const button = document.getElementById('copyButton');
                                    button.style.backgroundColor = '#4CAF50';
                                    button.innerText = 'Copied!';
                                    
                                    setTimeout(() => {{
                                        button.style.backgroundColor = '#654321';
                                        button.innerText = 'Copy JSON';
                                    }}, 2000);
                                }})
                                .catch(err => console.error('Failed to copy:', err));
                        }}
                        function clearCache(key) {{
                            console.log('Clearing key:', key);
                            fetch('/clear_cache', {{
                                method: 'POST',
                                headers: {{
                                    'Content-Type': 'application/json'
                                }},
                                body: JSON.stringify({{
                                    key: key
                                }})
                            }}).then(function(response) {{
                                if (response.ok) {{
                                    // Update the preview to show "No value found" message
                                    document.body.innerHTML = '<div style="font-size: 18px; color: #75715e; margin: 10px; font-family: monospace;">Cache Key Cleared</div>';
                                    
                                    // Properly trigger the refresh using Dash's setProps
                                    const refreshStore = window.parent.document.getElementById('refresh-trigger');
                                    if (refreshStore && refreshStore._dashprivate_) {{
                                        refreshStore._dashprivate_.setProps({{
                                            data: Date.now()
                                        }});
                                    }}
                                    
                                    // Clear the grid selection
                                    const gridDiv = window.parent.document.querySelector('.ag-theme-alpine-dark');
                                    if (gridDiv && gridDiv.__dashAgGridComponentFunctions) {{
                                        const api = gridDiv.__dashAgGridComponentFunctions.getApi();
                                        if (api) {{
                                            api.deselectAll();
                                        }}
                                    }}
                                }} else {{
                                    response.json().then(function(data) {{
                                        alert('Failed to clear cache key: ' + data.message);
                                    }});
                                }}
                            }}).catch(function(err) {{
                                console.error('Failed to clear cache:', err);
                                alert('Failed to clear cache: ' + err);
                            }});
                        }}
                    </script>
                </body>
            </html>
        """

        return html.Iframe(
            srcDoc=html_content,
            style={
                "width": "100%",
                "height": "100%",
                "border": "none",
                "backgroundColor": "#272822",
            },
        )

    return html.Div(
        "No value found for this key",
        style={
            "font-size": "18px",
            "color": "#75715e",
            "margin": "10px",
            "fontFamily": "monospace",
        },
    )


# Add a new callback to update the total keys count
@app.callback(Output("total-keys", "children"), Input("keys-table", "rowData"))
def update_total_keys(row_data):
    if row_data:
        return f"Total Keys: {len(row_data)}"
    return "Total Keys: 0"


def open_browser():
    time.sleep(1)
    port = 8050
    chrome_path = 'open -a "/Applications/Google Chrome.app" %s'
    webbrowser.get(chrome_path).open(f"http://127.0.0.1:{port}")


@app.server.route("/clear_cache", methods=["POST"])
def clear_cache():
    try:
        data = request.get_json()
        key = data.get("key")
        print(f"Clearing key: {key}")
        if key and cache_viewer.redis_instance.client:
            cache_viewer.redis_instance.client.delete(key)
            return jsonify({"status": "success", "cleared_key": key}), 200
        return jsonify({"status": "failure", "message": "Key not found"}), 400
    except Exception as e:
        return jsonify({"status": "failure", "message": str(e)}), 500


if __name__ == "__main__":
    # Update the way we start the server to avoid showing console
    import subprocess
    import sys

    if getattr(sys, "frozen", False):
        # Running as compiled exe
        threading.Thread(
            target=app.run_server,
            kwargs={
                "debug": True,
                "port": 8050,
                "dev_tools_hot_reload": True,  # Enable hot reloading
                "dev_tools_hot_reload_interval": 0.3,  # Check for changes every 0.3 seconds
                "dev_tools_hot_reload_watch_interval": 0.3,
                # Add these options to suppress console output
                "use_reloader": False,
                "dev_tools_ui": False,
                "dev_tools_props_check": False,
            },
        ).start()
        open_browser()
    else:
        # Running as script
        threading.Thread(
            target=app.run_server,
            kwargs={
                "debug": False,
                "port": 8050,
            },
        ).start()
        open_browser()
