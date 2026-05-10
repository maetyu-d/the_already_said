#!/usr/bin/env python3
from __future__ import annotations

import threading
import time
import webbrowser

from app import make_server


def main() -> None:
    server = make_server(host="127.0.0.1", port=0)
    host, port = server.server_address
    url = f"http://{host}:{port}"

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    webbrowser.open(url)

    print(f"The Already Said is running at {url}")
    print("Close this window to stop the local app server.")
    try:
        while thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
