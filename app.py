"""Web 看板：浏览器里实时看四个 Agent 接力。

Flask 只做两件事：后台线程跑 workflow，事件用 SSE 推给前端。
工作流逻辑和 CLI 共用 main.run，没有第二套实现。
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory

from main import run as run_workflow

load_dotenv()

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024

_events: "queue.Queue" = queue.Queue()
_running = threading.Event()
_lock = threading.Lock()


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/api/status")
def status():
    api_key = os.getenv("DASHSCOPE_API_KEY", "")
    return jsonify({
        "has_key": bool(api_key) and api_key != "sk-your-api-key-here",
        "running": _running.is_set(),
    })


@app.post("/api/run")
def start_run():
    global _events
    data = request.get_json(silent=True) or {}
    task = (data.get("task") or "").strip()
    if not task:
        return jsonify({"error": "任务不能为空"}), 400

    with _lock:
        if _running.is_set():
            return jsonify({"error": "已有任务在运行"}), 409
        _running.set()
        _events = queue.Queue()  # 每轮换一个新队列，旧连接自然失效
        q = _events

    model = data.get("model") or "qwen-plus"
    mock = bool(data.get("mock"))
    max_iterations = int(data.get("max_iterations") or 3)

    def worker():
        try:
            run_workflow(
                task,
                runs_dir="runs",
                max_iterations=max_iterations,
                mock=mock,
                model=model,
                event_sink=q.put,
            )
        except SystemExit as exc:
            q.put({"event": "error", "error": str(exc)})
        except Exception as exc:
            q.put({"event": "error", "error": f"{type(exc).__name__}: {exc}"})
        finally:
            _running.clear()
            q.put({"event": "stream_end"})

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/stream")
def stream():
    q = _events

    def generate():
        while True:
            record = q.get()
            yield f"data: {json.dumps(record, ensure_ascii=False)}\n\n"
            if record.get("event") == "stream_end":
                break

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


if __name__ == "__main__":
    port = 5001
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    app.run(host="127.0.0.1", port=port, threaded=True)
