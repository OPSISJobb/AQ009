"""
AQ009 - styrning av det 9-positioners hjulet.

Tva granssnitt mot samma motor:
  * RS232 (serial_listener.py) - kommandon fran AR, det som styr i drift.
  * Webbsida (denna fil) - manuell korning, hemkorning och kalibrering.

Startas med:  python3 app.py
Sida:         http://<pi-adress>:8080/   (eller localhost pa samma maskin)
"""

import atexit
import logging

from flask import Flask, render_template
from flask_socketio import SocketIO

import config
import status_led
from stepper import MotorController
from serial_listener import SerialListener

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("app")

app = Flask(__name__)
app.config["SECRET_KEY"] = "aq009"  # ingen extern atkomst forutsatt, ok som den ar
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

motor = MotorController()
serial_listener = SerialListener(motor)


def _broadcast_state(state: dict):
    socketio.emit("state", state)


motor.on_state_change = _broadcast_state


@app.route("/")
def index():
    return render_template("index.html")


@socketio.on("connect")
def on_connect():
    socketio.emit("state", motor.get_state())


@socketio.on("goto")
def on_goto(data):
    try:
        position = int(data.get("position"))
    except (TypeError, ValueError, AttributeError):
        return
    if 1 <= position <= config.NUM_POSITIONS:
        motor.goto_position(position)


@socketio.on("home")
def on_home(_data=None):
    motor.home()


@socketio.on("jog")
def on_jog(data):
    try:
        delta = int(data.get("delta"))
    except (TypeError, ValueError, AttributeError):
        return
    motor.jog(delta)


@socketio.on("save_calibration")
def on_save_calibration(data):
    try:
        position = int(data.get("position"))
    except (TypeError, ValueError, AttributeError):
        return
    if 1 <= position <= config.NUM_POSITIONS:
        motor.save_calibration_here(position)


@socketio.on("emergency_stop")
def on_emergency_stop(_data=None):
    motor.emergency_stop()


@socketio.on("get_state")
def on_get_state(_data=None):
    socketio.emit("state", motor.get_state())


def _shutdown():
    logger.info("Stanger ner - stoppar serial-lyssnare och motor")
    serial_listener.stop()
    motor.shutdown()
    status_led.off()


atexit.register(_shutdown)


if __name__ == "__main__":
    # Ingen hemkorning vid uppstart. Hjulet ska ALDRIG ga till hemlaget av
    # sig sjalvt - bara nar AR skickat hela foljden 00,10,20,30 (setup-menyn)
    # eller 0101,1101,2101,3101 (over till kalibreringslage), se
    # MENU_ENTRY_SEQUENCE/KALIBRERINGS_SEQUENCE i serial_listener.py, eller
    # nar nagon trycker Hemkorning pa webbsidan. Tills dess ar laget okant och
    # goto-kommandon avvisas med "Ej hemkord" (stepper.py _do_goto).
    logger.info(
        "Startar RS232-lyssnaren utan hemkorning - hjulet star kvar tills "
        "AR skickat 00,10,20,30 eller 0101,1101,2101,3101"
    )
    # Tand statuslampan - tyst om dioden saknas eller inte fungerar
    status_led.on()
    serial_listener.start()
    logger.info(
        "Startar webbserver pa http://%s:%d", config.WEB_HOST, config.WEB_PORT,
    )
    socketio.run(app, host=config.WEB_HOST, port=config.WEB_PORT, allow_unsafe_werkzeug=True)
