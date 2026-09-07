"""
Konfiguration for AQ009 - stegmotor-styrningen av det 9-positioners hjulet.

Andra varden har HAR (t.ex. GPIO-pinnar, steg/varv, seriellport) sa de
matchar din verkliga koppling. Allt anvands av stepper.py, serial_listener.py
och app.py.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# GPIO (BCM-numrering). Drivern har en STEP-signal (puls = ett steg) och en
# DIR-signal (niva = riktning). Ingen ENABLE-ingang - drivern ar alltid aktiv.
# ---------------------------------------------------------------------------
GPIO_CHIP = 0          # /dev/gpiochip0 pa Raspberry Pi 4
STEP_PIN = 12          # Pulsutgang till drivern (ett pulses = ett steg)
DIR_PIN = 16            # Riktningsutgang till drivern
HOME_SENSOR_PIN = 26   # Ingang fran hemlages-triggern

# Om DIR-pinnens niva ger fel rotationsriktning, satt denna till True for
# att vanda pa den utan att andra nagot annat.
DIR_INVERT = False

# Ar hemsensorn aktiv (utlost) vid LOG (0) eller HOG (1) niva?
# Vanligast for mekaniska/optiska brytare med pull-up ar aktiv LAG.
HOME_SENSOR_ACTIVE_LOW = True

# Interna pull-motstand pa Pi:n for sensoringangen: "up", "down" eller None
# om du redan har externt motstand.
HOME_SENSOR_PULL = "up"

# ---------------------------------------------------------------------------
# Mekanik
# ---------------------------------------------------------------------------
# Motor: Smooth (Ningbo Smooth Electric) 23HD604Y-T20, FH171027 - NEMA23,
# 23HD-serien = 1,8 grader/steg (200 fullsteg/varv).
#
# Totalt antal steg per varv PA DRIVERNS INGANG (fullsteg * mikrostegsvarde
# installt pa drivern). Uppmatt med test_microstepping.py (2026-08-14):
# drivern kor 2x mikrostegning, dvs 200 * 2 = 400 - inte 16x som tidigare
# antogs. Verklig position per lage justeras sedan via kalibrering.
STEPS_PER_REV = 400

NUM_POSITIONS = 9

# Drivern har DIR-styrning (se DIR_PIN ovan), men "ga till position" och
# hemkorning anvander fortfarande bara framatriktningen (okande stegrakning)
# - hemkorningens precision bygger pa att alltid narma sig sensorn fran
# samma hall (se _do_home i stepper.py). Bakatriktningen anvands just nu
# bara for manuell jogg i kalibreringslaget.

# ---------------------------------------------------------------------------
# Hastigheter (i steg/sekund). Halls lagt under kalibrering/hemkorning for
# noggrannhet, hogre for normal korning.
#
# Sankta 8x (2026-08-14) efter att STEPS_PER_REV andrades fran 3200 till 400
# (uppmatt 2x mikrostegning istallet for antagna 16x) - annars hade samma
# steg/sekund-varden gett 8x snabbare fysisk rotation an tidigare.
# ---------------------------------------------------------------------------
RUN_SPEED_STEPS_PER_SEC = 100
JOG_SPEED_STEPS_PER_SEC = 25              # anvands vid kalibrerings-jogg

# Hemkorning kan inte backa och narma sig igen (ingen DIR), sa hela sokningen
# gors steg-for-steg i denna hastighet for att traffa hemsensorns lage
# exakt likadant varje gang.
HOMING_SPEED_STEPS_PER_SEC = 25

# Sakerhetsgrans: om hemkorning inte hittar sensorn inom detta antal steg,
# avbryt med fel (skyddar mot att motorn snurrar oandligt om sensorn saknas
# eller ar trasig).
HOMING_MAX_STEPS = STEPS_PER_REV * 2

# ---------------------------------------------------------------------------
# RS232
# ---------------------------------------------------------------------------
# Satt till False for att helt stanga av RS232-lyssnaren (t.ex. innan
# kabeln/adaptern ar inkopplad) - annars loggas en varning var 5:e sekund
# medan den forgaves forsoker oppna SERIAL_PORT.
SERIAL_ENABLED = True

# Valj kalla har: True = anslut ut till DOSBox over natverk (SERIAL_IP_URL),
# False = anvand en lokal port pa Pi:n (SERIAL_LOCAL_PORT), t.ex. en HAT:ens
# inbyggda UART eller en USB-RS232-adapter. Andra bara denna flagga for att
# vaxla - ingen annan kodandring behovs.
USE_IP = False

# Anvands nar USE_IP = False. /dev/ttySC0 ar den lokala COM-porten pa
# HAT:en. (USB-RS232-adapter brukar istallet bli /dev/ttyUSB0.)
SERIAL_LOCAL_PORT = "/dev/ttySC0"

# Anvands nar USE_IP = True. Kablar man inte alls, utan kor DOS-programmet
# i DOSBox pa en Windows-dator i samma natverk: SERIAL_IP_URL ska vara
# "socket://<windows-dator-ip>:5000" (DOSBox konfigureras da att lyssna,
# se dosbox.conf: "serial1=nullmodem port:5000 transparent:1" - ingen
# server:-parameter dar, sa DOSBox lyssnar och Pi:n ansluter ut hit).
SERIAL_IP_URL = "socket://192.168.10.47:5000"

# pyserial's serial_for_url() (anvands i serial_listener.py) hanterar bade
# vanliga enhetssokvagar och socket://-URL:er automatiskt.
SERIAL_PORT = SERIAL_IP_URL if USE_IP else SERIAL_LOCAL_PORT
SERIAL_BAUDRATE = 9600
SERIAL_BYTESIZE = 8
SERIAL_PARITY = "N"
SERIAL_STOPBITS = 1
SERIAL_TIMEOUT = 1.0

# Protokoll for inkommande kommando (aterskapat ur sniffad AR<->CU007-trafik -
# det verkliga protokollet, inte pahittat): en rad text per kommando, avslutad
# med \r (INTE \n). Se serial_listener.py-docstringen och README.md:
#   "00"          -> setup/ping, svar "001.0"
#   "01<C>1"      -> oppna cylinder <C> (1-9) -> goto_position(<C>)
#   "01<C>0"      -> stang cylinder <C> - ingen rorelse, bara ack
#   "<E>10<A>"    -> lagesvaxling till enhet <E> - ingen rorelse, bara ack
# Justera parse_command() i serial_listener.py om din motpart skickar
# nagot annat format (t.ex. binart/enstaka byte).


# Hur AQ009 kvitterar AR:s kommandon. Riktig CU007 har setts svara pa tva satt,
# och det verkar vara variant/version snarare an olika betydelse - darfor gar
# bada att valja har:
#
#   SERIAL_ACK_SUFFIX:  "1.0" (sa svarade den riktiga ladan) eller "2.0"
#   SERIAL_ACK_FORMAT:  "eko" -> hela kommandot ekas oforandrat:
#                                "0110" kvitteras "01101.0"
#                       "00"  -> avsandarfaltet byts till "00":
#                                "0110" kvitteras "00102.0" (aldre varianten)
#
# Standard ar "eko" + "1.0" - det ar exakt vad den riktiga CU007-ladan
# skickade i sniffad trafik ("001.0", "01101.0", "01011.0"; den anvande aldrig
# "2.0"). Da ser AQ009:s svar likadana ut som den gamla ladans.
#
# Tvasiffriga ping-koder kvitteras likadant i bada formaten: koden + suffix.
# Med "1.0" blir det "00" -> "001.0" och "10" -> "101.0", dvs samma monster
# som enhet 0 svarar med, fast fran enhet 1.
SERIAL_ACK_SUFFIX = "1.0"
SERIAL_ACK_FORMAT = "eko"

# ---------------------------------------------------------------------------
# Ovrigt
# ---------------------------------------------------------------------------
CALIBRATION_FILE = os.path.join(BASE_DIR, "data", "calibration.json")
WEB_HOST = "0.0.0.0"
WEB_PORT = 8080
