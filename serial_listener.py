"""
RS232-lyssnare: tar emot kommandon fran den externa styrningen ("AR") och
vidarebefordrar dem till MotorController. Hjulets 9 positioner motsvarar
AR:s "cylindrar".

Protokoll (aterskapat ur sniffad AR<->CU007-trafik - detta ar det verkliga
protokollet fran motparten, inte nagot vi har hittat pa): en textrad per
kommando, avslutas med '\\r' (INTE '\\n').

    AR -> oss:
        "<XX>"        -> tvasiffrig "ping"-kod, t.ex. "00" (AR oppnar sin
                         setup-meny) eller "10"/"20"/"30". Forsta siffran ar
                         enhetsadressen, sa det ar samma ping till enhet 0-3.
                         Vi ekar bara tillbaka koden. Nar AR skickar "00",
                         "10", "20", "30" i den ordningen triggas en
                         hemkorning, se SerialListener.MENU_ENTRY_SEQUENCE.
        "01<C><A>"    -> cylinderkommando: <C> = cylindernummer (1-9),
                         <A> = "1" oppna, "0" stang.
                         Ex: "0171" = oppna cylinder 7, "0170" = stang
                         cylinder 7. AR skickar alltid stang pa foregaende
                         cylinder innan den skickar oppna pa nasta, och
                         skickar varje kommando 3 ggr i rad (dess egen
                         bekraftelselogik - vi svarar bara pa varje rad).
        "<E>10<A>"    -> lagesvaxling: cylinder 0 finns inte fysiskt, sa
                         den platsen anvands for att vaxla mellan matnings-
                         och kalibreringslage. <E> ar enhetsadressen och <A>
                         = "1" kalibrering, "0" matning. AR skickar hela
                         raddan till enhet 0-3: "0101", "1101", "2101",
                         "3101" (samma adressmonster som pingen 00/10/20/30).
                         Nar hela den foljden setts triggas en hemkorning -
                         se SerialListener.KALIBRERINGS_SEQUENCE - sa hjulet
                         star i hemlaget nar kalibreringen borjar. Ingen
                         hjulrorelse i ovrigt.
        "11<N><A>"    -> specialfall for cylinder 8-9: AR foljer inte
                         "01<C><A>"-monstret for dessa tva, utan skickar
                         "11" + N (= cylinder - 7) + <A>. Ex: "1111"/"1110"
                         = oppna/stang cylinder 8, "1121"/"1120" =
                         oppna/stang cylinder 9.

    Oss -> AR (svar pa varje mottagen rad), se SERIAL_ACK_FORMAT och
    SERIAL_ACK_SUFFIX i config.py:
        "eko" + "1.0" -> hela kommandot ekas: "0110" -> "01101.0",
                         "00" -> "001.0", "10" -> "101.0". Detta ar
                         standard: det ar exakt sa den riktiga CU007-ladan
                         svarade i sniffad trafik.
        "00" + "2.0"  -> avsandarfaltet byts till "00": "0110" -> "00102.0".
                         Den aldre varianten, finns kvar om AR vill ha den.

VIKTIGT: forsta siffran i ett kommando ar enhetsadress. Den gamla
CU007-ladan var BARA enhet 0: den svarade pa "00", "01<C><A>" och "010<A>",
och teg helt pa "10", "20", "30", "1101", "2101", "3101" och "11<N><A>".
AR gjorde da 3 forsok med 2 s mellanrum per kommando innan den gick vidare.
AQ009 ersatter hela kedjan och svarar darfor som samtliga enheter, dvs
ackar aven det den gamla ladan var tyst pa.

"Stang cylinder" flyttar inte hjulet (det har bara en aktiv position at
gangen) - bara ack:as. "Oppna cylinder N" -> motor.goto_position(N).

Porten oppnas med automatiska ateranslutningsforsok, sa tjansten kan startas
innan motparten (RS232-kabeln) ar ansluten.
"""

import logging
import threading
import time

import serial

import config

logger = logging.getLogger(__name__)


def parse_command(line: str):
    """Tolkar en rad fran AR.

    Returnerar ("ping", kod), ("lage", (enhet, action)),
    ("cylinder", (cyl, action)) eller None.
    """
    text = line.strip()
    if not text:
        return None
    if (len(text) == 4 and text[0] in "0123" and text[1] == "1"
            and text[2] == "0" and text[3] in ("0", "1")):
        # Lagesvaxling "<enhet>10<A>": cylinder 0 finns inte fysiskt, siffran
        # ar enhetsadressen. AR skickar hela raddan 0101, 1101, 2101, 3101 -
        # samma adressmonster som pingen 00/10/20/30 - nar operatoren gar over
        # till kalibreringslage. Hela foljden triggar hemkorning, se
        # SerialListener.KALIBRERINGS_SEQUENCE.
        return ("lage", (int(text[0]), text[3]))
    if len(text) == 4 and text[:2] == "11" and text[2] in ("1", "2") and text[3].isdigit():
        # AR foljer inte "01<C><A>"-monstret for cylinder 8-9 - den anvander
        # "11<N><A>" dar N = cylinder - 7 (dvs "1111"/"1110" for cylinder 8,
        # "1121"/"1120" for cylinder 9). N begransas till 1-2 (endast cylinder
        # 8-9 finns) sa t.ex. "1101" inte felaktigt tolkas som cylinder 7 (som
        # redan tacks av "01<C><A>"-monstret) - "1101" ar en lagesvaxling.
        cyl = int(text[2]) + 7
        action = text[3]
        return ("cylinder", (cyl, action))
    if len(text) == 2 and text.isdigit():
        # Tvasiffriga koder: pingen till enhet 0-3 ("00", "10", "20", "30").
        # Ingen av dem flyttar hjulet - vi ekar bara tillbaka koden + suffix.
        return ("ping", text)
    if len(text) == 4 and text[:2] == "01" and text[2:].isdigit():
        cyl = int(text[2])
        action = text[3]
        return ("cylinder", (cyl, action))
    return None


class Foljd:
    """Haller reda pa en foljd av koder som AR skickar i en bestamd ordning.

    AR upprepar samma kod var 2:e sekund (3 forsok) tills den far svar, sa en
    upprepning av den senast godkanda koden ar inget sekvensbrott utan ska
    hoppas over. Utan det nollstalls raknaren pa forsta omforsoket och foljden
    fullbordas aldrig.

    Koder som inte hor till foljden alls matas aldrig in har - varje foljd far
    bara sin egen sorts kod - sa ovrig trafik daremellan bryter den inte.
    """

    def __init__(self, koder):
        self.koder = tuple(koder)
        self._steg = 0

    def mata(self, kod: str) -> bool:
        """Returnerar True nar hela foljden setts i ratt ordning."""
        if self._steg > 0 and kod == self.koder[self._steg - 1]:
            return False  # omforsok pa den senast godkanda koden
        if kod != self.koder[self._steg]:
            # Bryt inte foljden om koden rakar vara borjan pa en ny.
            self._steg = 1 if kod == self.koder[0] else 0
            return False
        self._steg += 1
        if self._steg < len(self.koder):
            return False
        self._steg = 0
        return True


class SerialListener:
    # AR skickar dessa fyra pingkoder i foljd nar man gar in i setup-menyn.
    # Nar hela sekvensen setts i ratt ordning kors en hemkorning.
    MENU_ENTRY_SEQUENCE = ("00", "10", "20", "30")

    # Lagesvaxling till kalibreringslage, utskickad till enhet 0-3. Aven denna
    # foljd kor hemkorning - hjulet ska sta i hemlaget nar kalibreringen borjar.
    KALIBRERINGS_SEQUENCE = ("0101", "1101", "2101", "3101")

    def __init__(self, motor_controller):
        self.motor = motor_controller
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._ser = None
        self._menyfoljd = Foljd(self.MENU_ENTRY_SEQUENCE)
        self._kalibreringsfoljd = Foljd(self.KALIBRERINGS_SEQUENCE)

    def start(self):
        if not config.SERIAL_ENABLED:
            logger.info("RS232-lyssnare avstangd (config.SERIAL_ENABLED = False)")
            return
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass

    def _open_port(self):
        url = config.SERIAL_PORT
        while not self._stop_event.is_set():
            try:
                ser = serial.serial_for_url(
                    url,
                    baudrate=config.SERIAL_BAUDRATE,
                    bytesize=config.SERIAL_BYTESIZE,
                    parity=config.SERIAL_PARITY,
                    stopbits=config.SERIAL_STOPBITS,
                    timeout=config.SERIAL_TIMEOUT,
                )
                logger.info("RS232-port %s oppnad (%d baud)", url, config.SERIAL_BAUDRATE)
                return ser
            except Exception as exc:
                logger.warning(
                    "Kunde inte oppna RS232-port %s (%s) - forsoker igen om 5s",
                    url, exc,
                )
                self._stop_event.wait(5.0)
        return None

    def _run(self):
        while not self._stop_event.is_set():
            self._ser = self._open_port()
            if self._ser is None:
                return
            try:
                while not self._stop_event.is_set():
                    # AR avslutar rader med '\r' (inte '\n'), och kan skicka
                    # samma kommando 3 ggr i snabb takt - readline() skulle
                    # splitta pa '\n' och kunde slippa ihop flera kommandon
                    # till en rad om de hinner komma innan timeout.
                    raw = self._ser.read_until(b"\r")
                    if not raw:
                        continue
                    try:
                        line = raw.decode("ascii", errors="replace")
                    except Exception:
                        continue
                    logger.info(
                        "AR -> AQ009: %r (bytes: %s)",
                        line, " ".join(f"{b:02x}" for b in raw),
                    )
                    self._handle_line(line)
            except Exception as exc:
                if self._stop_event.is_set():
                    return  # porten stangdes av stop() - normal nedstangning
                if isinstance(exc, serial.SerialException):
                    logger.warning("RS232-fel (%s) - forsoker oppna om igen", exc)
                else:
                    logger.exception("Ovantat fel i RS232-lyssnare")
                time.sleep(2.0)

    def _ack(self, line: str, kropp: str) -> str:
        """Bygger kvittensen enligt config: eko av kommandot eller 00-formen.

        Riktig CU007 sags svara "01101.0" (hela kommandot ekat, suffix "1.0")
        i sniffad trafik, medan den aldre loggen visar "00102.0" - se
        SERIAL_ACK_FORMAT/SERIAL_ACK_SUFFIX i config.py.
        """
        suffix = getattr(config, "SERIAL_ACK_SUFFIX", "1.0")
        if getattr(config, "SERIAL_ACK_FORMAT", "eko") == "eko":
            return f"{line.strip()}{suffix}\r"
        return f"{kropp}{suffix}\r"

    def _handle_line(self, line: str):
        cmd = parse_command(line)
        if cmd is None:
            logger.warning("Okant RS232-kommando: %r", line)
            return
        kind, arg = cmd
        if kind == "ping":
            if self._menyfoljd.mata(arg):
                self._hemkorning("AR gick in i menyn", self.MENU_ENTRY_SEQUENCE)
            # Ping kvitteras likadant i bada formaten: koden + suffix.
            self._write_response(self._ack(line, arg))
            return
        if kind == "lage":
            enhet, action = arg
            if self._kalibreringsfoljd.mata(line.strip()):
                self._hemkorning(
                    "AR gick over till kalibreringslage", self.KALIBRERINGS_SEQUENCE
                )
            else:
                lage = "kalibrering" if action == "1" else "matning"
                logger.info(
                    "Lagesvaxling till %s, enhet %d - ingen hjulrorelse", lage, enhet
                )
            # I "00"-formatet finns ingen plats for enhetsadressen (den aldre
            # varianten kanner bara enhet 0), sa alla fyra kvitteras som
            # cylinder 0. I "eko"-formatet ekas raden som den ar, vilket ar
            # det riktig CU007 gor pa "0101" (-> "01011.0").
            self._write_response(self._ack(line, f"000{action}"))
            return
        cyl, action = arg
        if action == "1" and 1 <= cyl <= config.NUM_POSITIONS:
            logger.info("Oppna cylinder %d -> goto_position(%d)", cyl, cyl)
            self.motor.goto_position(cyl)
        elif action == "1":
            logger.info(
                "Oppna cylinder %d - utanfor 1-%d, ingen rorelse",
                cyl, config.NUM_POSITIONS,
            )
        else:
            logger.info("Stang cylinder %d - ingen rorelse", cyl)
        self._write_response(self._ack(line, f"00{cyl}{action}"))

    def _hemkorning(self, anledning: str, foljd) -> None:
        logger.info(
            "%s (%s mottaget i foljd) -> kor hemkorning",
            anledning, " ".join(foljd),
        )
        self.motor.home()

    def _write_response(self, text: str):
        if self._ser is None:
            return
        logger.info("AQ009 -> AR: %r", text)
        try:
            self._ser.write(text.encode("ascii"))
        except Exception:
            logger.exception("Kunde inte skriva till AR-porten")
