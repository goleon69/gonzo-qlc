#!/usr/bin/env python3
"""Prints incoming OSC messages - to test Gonzo without QLC+.
Close QLC+ first (same port).  Usage: python osc_monitor.py [port]"""
import sys
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import BlockingOSCUDPServer

port = int(sys.argv[1]) if len(sys.argv) > 1 else 7700
d = Dispatcher()
d.set_default_handler(lambda addr, *a: print(f"{addr}  {a}", flush=True))
print(f"Listening for OSC on 127.0.0.1:{port} (Ctrl+C to stop)")
BlockingOSCUDPServer(("127.0.0.1", port), d).serve_forever()
