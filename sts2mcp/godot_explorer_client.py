"""TCP client for communicating with the GodotExplorer mod running inside the game.

GodotExplorer is a Godot scene inspector/debugger mod that runs an MCP-compatible
TCP server on port 27020.  This client sends JSON-RPC 2.0 `tools/call` requests
and returns the parsed text results.
"""

import json
import socket
from typing import Any

import os
import re

def _get_explorer_candidates():
    """Detect possible explorer hosts (Windows IPs from WSL, and localhost)."""
    candidates = []
    
    # Allow environment variable override
    env_host = os.environ.get("STS2_EXPLORER_HOST")
    if env_host:
        return [env_host]

    # In WSL, try to find the host IPs
    if os.path.exists("/proc/version"):
        with open("/proc/version", "r") as f:
            if "microsoft" in f.read().lower():
                # Try ip route (gateway)
                try:
                    result = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True)
                    if result.returncode == 0:
                        match = re.search(r"default via ([\d\.]+)", result.stdout)
                        if match:
                            addr = match.group(1)
                            if addr not in candidates: candidates.append(addr)
                except Exception:
                    pass

                # Try common WSL subnets if not found
                for subnet_prefix in ["172.17.16", "172.26.80", "192.168.240"]:
                    addr = f"{subnet_prefix}.1"
                    if addr not in candidates: candidates.append(addr)

                # Fallback to resolv.conf
                try:
                    with open("/etc/resolv.conf", "r") as r:
                        content = r.read()
                        match = re.search(r"nameserver\s+([\d\.]+)", content)
                        if match:
                            addr = match.group(1)
                            if addr not in candidates: candidates.append(addr)
                except Exception:
                    pass

    if "127.0.0.1" not in candidates:
        candidates.append("127.0.0.1")
    return candidates

EXPLORER_PORT = 27020
TIMEOUT = 15.0
MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10 MB
RECV_BUFFER_SIZE = 4096

_next_id = 0


def _get_id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def _send_rpc(method: str, params: dict | None = None) -> dict:
    """Send a JSON-RPC request to GodotExplorer and return the parsed response."""
    candidates = _get_explorer_candidates()
    last_error = None
    
    for host in candidates:
        request = {
            "jsonrpc": "2.0",
            "id": _get_id(),
            "method": method,
        }
        if params is not None:
            request["params"] = params

        try:
            # Use shorter timeout for probing connections
            probe_timeout = 2.0 if len(candidates) > 1 else TIMEOUT
            with socket.create_connection((host, EXPLORER_PORT), timeout=probe_timeout) as sock:
                sock.settimeout(TIMEOUT)
                payload = json.dumps(request) + "\n"
                sock.sendall(payload.encode("utf-8"))

                # Read newline-delimited response
                data = b""
                while True:
                    chunk = sock.recv(RECV_BUFFER_SIZE)
                    if not chunk:
                        break
                    data += chunk
                    if len(data) > MAX_RESPONSE_SIZE:
                        return {"error": f"Response exceeded {MAX_RESPONSE_SIZE} bytes"}
                    if b"\n" in data:
                        break

            text = data.decode("utf-8-sig").strip()
            if not text:
                continue # Try next candidate
                
            return json.loads(text)

        except (ConnectionRefusedError, socket.timeout, OSError) as e:
            last_error = e
            continue
            
    # If we get here, all candidates failed
    return {
        "error": (
            f"GodotExplorer unreachable on {candidates}. "
            f"Last error: {type(last_error).__name__}: {last_error}. "
            "Please ensure game is running and GodotExplorer mod is enabled."
        )
    }


def _call_tool(tool_name: str, arguments: dict | None = None) -> dict:
    """Call a GodotExplorer tool and return the result.

    Returns a dict with either:
      - "text": the tool's text output
      - "error": an error message
    """
    params: dict[str, Any] = {"name": tool_name}
    if arguments:
        params["arguments"] = arguments

    response = _send_rpc("tools/call", params)

    # Handle JSON-RPC level errors
    if "error" in response and isinstance(response["error"], str):
        return response
    if "error" in response and isinstance(response["error"], dict):
        return {"error": response["error"].get("message", str(response["error"]))}

    # Extract MCP tool result
    result = response.get("result", {})
    if isinstance(result, dict):
        content = result.get("content", [])
        is_error = result.get("isError", False)
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("text")]
        text = "\n".join(texts) if texts else json.dumps(result)
        if is_error:
            return {"error": text}
        return {"text": text}

    return {"text": str(result)}


def _parse_text(result: dict) -> str | dict:
    """Convert a _call_tool result to either parsed JSON or raw text."""
    if "error" in result:
        return result
    text = result.get("text", "")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


# ── Tool wrappers ──────────────────────────────────────────────────────────


def ping() -> dict:
    """Check if GodotExplorer is reachable."""
    return _send_rpc("ping")


def is_connected() -> bool:
    """Check if GodotExplorer is reachable."""
    try:
        result = ping()
        return "error" not in result
    except Exception:
        return False


def get_scene_tree(depth: int = 3, root_path: str = "/root"):
    args: dict[str, Any] = {}
    if depth != 3:
        args["depth"] = depth
    if root_path != "/root":
        args["root_path"] = root_path
    return _parse_text(_call_tool("get_scene_tree", args or None))


def find_nodes(pattern: str, type_filter: str = "", limit: int = 50):
    args: dict[str, Any] = {"pattern": pattern}
    if type_filter:
        args["type"] = type_filter
    if limit != 50:
        args["limit"] = limit
    return _parse_text(_call_tool("find_nodes", args))


def inspect_node(path: str):
    return _parse_text(_call_tool("inspect_node", {"path": path}))


def get_property(path: str, property_name: str):
    return _parse_text(_call_tool("get_property", {"path": path, "property": property_name}))


def set_property(path: str, property_name: str, value: str):
    return _parse_text(_call_tool("set_property", {"path": path, "property": property_name, "value": value}))


def call_method(path: str, method: str, method_args: str = ""):
    args: dict[str, Any] = {"path": path, "method": method}
    if method_args:
        args["args"] = method_args
    return _parse_text(_call_tool("call_method", args))


def toggle_visibility(path: str):
    return _parse_text(_call_tool("toggle_visibility", {"path": path}))


def get_node_count():
    return _parse_text(_call_tool("get_node_count"))


def list_groups(group: str = ""):
    args = {"group": group} if group else None
    return _parse_text(_call_tool("list_groups", args))


def get_game_info():
    return _parse_text(_call_tool("get_game_info"))


def list_assemblies():
    return _parse_text(_call_tool("list_assemblies"))


def search_types(query: str):
    return _parse_text(_call_tool("search_types", {"query": query}))


def inspect_type(type_name: str):
    return _parse_text(_call_tool("inspect_type", {"type_name": type_name}))


def tween_property(
    path: str,
    property_name: str,
    to: str,
    from_val: str = "",
    duration: str = "1.0",
    loops: int = 0,
    trans: str = "linear",
):
    args: dict[str, Any] = {"path": path, "property": property_name, "to": to}
    if from_val:
        args["from"] = from_val
    if duration != "1.0":
        args["duration"] = duration
    if loops != 0:
        args["loops"] = loops
    if trans != "linear":
        args["trans"] = trans
    return _parse_text(_call_tool("tween_property", args))
