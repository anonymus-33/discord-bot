import discord
from discord.ext import commands
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import re
import aiohttp
from aiohttp import web
import asyncio
from discord.ext import tasks
import base64
import binascii

cred_base64 = os.getenv("GOOGLE_SHEETS_CREDENTIALS")

if not cred_base64:
    raise ValueError("La variable de entorno GOOGLE_SHEETS_CREDENTIALS no está configurada.")

try:
    decoded_bytes = base64.b64decode(cred_base64)
except binascii.Error as e:
    raise ValueError(f"Error al decodificar GOOGLE_SHEETS_CREDENTIALS: {e}")

with open("cred.json", "wb") as f:
    f.write(decoded_bytes)

# ------------ CONFIGURACION ------------
CANAL_CONTROL_ID = 1368713365836398662  # Canal control partners

GOOGLE_SHEET_NAME = "Control rendimiento TC"
CRED_FILE = "cred.json"

PARTNERS_CHANNELS = {
    1354816506298503468: "astra-community",
    1259150196911116340: "maes-house",
    1248387513685639178: "cats-world",
    1249427278560235650: "love-gaming",
    1249469388109778964: "glitch-galaxy",
    1255178937307365440: "stellar-melody",
    1261378669822214204: "blox-fruits",
    1305652275221626880: "casita-randoms",
    1334517927633883156: "cyberworld",
    1357009326073708737: "the-garden-isekai",
    1357095659899195644: "love-and-chill",
    1357268556915671210: "kaylius",
    1358095303709954109: "ci-unsc",
    1358094348721324162: "cofee-time",
    1358101659498188962: "star-night",
    1358454649568493730: "gatetes",
    1363863239363919952: "búnker-del-hikikomori",
    1367786043482308658: "akane",
    1367890715404669080: "wave-world",
    1367891891844288523: "koreami",
    1369100655301759048: "star-lights",
    1367971976005554330: "egirl-paradise",
    1369107360907526155: "anime-world",
    1369113967095582820: "banananef-team",
    1371529312406339705: "luniverzone",
    1378790397148401774: "el-nido-de-cuervo",
    1385812628282282014: "empanada-chat"
}

# ------------ GOOGLE SHEETS SETUP ------------
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/spreadsheets",
         "https://www.googleapis.com/auth/drive.file",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, scope)
client_gs = gspread.authorize(creds)

# ------------ DISCORD BOT SETUP ------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------ FUNCIONES AUXILIARES ------------

def buscar_fila(partner, sheet):
    registros = sheet.get_all_records()
    for i, row in enumerate(registros, start=2):
        if row.get("Persona") == partner:
            return i
    return None

def actualizar_o_crear_fila(datos):
    # Abrimos hoja según partner guardado en datos["partner"]
    partner = datos["partner"]
    hoja_nombre = None
    # Buscar el canal que tenga este partner
    for canal_id, nombre in PARTNERS_CHANNELS.items():
        if nombre == partner:
            hoja_nombre = nombre
            break
    if not hoja_nombre:
        hoja_nombre = "blox-fruits"  # fallback

    sheet = client_gs.open(GOOGLE_SHEET_NAME).worksheet(hoja_nombre)

    registros = sheet.get_all_records()
    fila_existente = None
    fecha_hoy = datetime.utcnow().strftime("%Y-%m-%d")

    # Buscar fila con el mismo partner y última renovación igual a hoy
    for i, row in enumerate(registros, start=2):
        if row.get("Persona") == partner and row.get("Última renovación") == fecha_hoy:
            fila_existente = i
            break

    # Determinar estado
    if all(datos[key] == "Sí" for key in ["plantilla", "everyone", "timestamp", "mencion"]):
        estado = "RENOVADO"
    else:
        estado = "PENDIENTE"

    fila_data = [
        partner,              # Persona (col A)
        fecha_hoy,            # Última renovación (col B)
        datos["proxima"],     # Próxima renovación (col C)
        estado,               # Estado de renovación (col D)
        datos["plantilla"],   # Ha Enviado Plantilla (col E)
        datos["everyone"],    # Ping @everyone (col F)
        datos["timestamp"],   # Timestamp correcto (col G)
        datos["mencion"]      # Me ha mencionado (col H)
    ]

    if fila_existente:
        sheet.update(f"A{fila_existente}:H{fila_existente}", [fila_data])
        if estado == "RENOVADO":
            sheet.format(f"D{fila_existente}", {
                "backgroundColor": {"red": 0, "green": 1, "blue": 0},
                "textFormat": {"bold": True}
            })
        else:
            sheet.format(f"D{fila_existente}", {
                "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                "textFormat": {"bold": False}
            })
        sheet.format(f"E{fila_existente}", {
            "textFormat": {"bold": False}
        })
    else:
        sheet.append_row(fila_data)
        fila_nueva = sheet.row_count
        if estado == "RENOVADO":
            sheet.format(f"D{fila_nueva}", {
                "backgroundColor": {"red": 0, "green": 1, "blue": 0},
                "textFormat": {"bold": True}
            })
        sheet.format(f"E{fila_nueva}", {
            "textFormat": {"bold": False}
        })

def extraer_timestamp(texto):
    match = re.search(r"<t:(\d+):R>", texto)
    if match:
        ts = int(match.group(1))
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    return None

def es_plantilla(mensaje):
    patron = r"https?://(discord\.gg|discord\.com/invite|discordapp\.com/invite)/[a-zA-Z0-9]+"
    return re.search(patron, mensaje) is not None

# ------------ EVENTOS DEL BOT ------------
datos_temp = {}

@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="el control de los partners de TC 🐢"))

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    canal_id = message.channel.id
    contenido = message.content
    autor = str(message.author)
    fecha_hoy = datetime.utcnow().strftime("%Y-%m-%d")

    if canal_id == CANAL_CONTROL_ID:
        match = re.search(r"<@(\d+)>\s*<#(\d+)>", contenido)
        if match:
            mencion_id, ref_canal_id = match.groups()
            ref_canal_id = int(ref_canal_id)
            if ref_canal_id in datos_temp:
                datos_temp[ref_canal_id]["mencion"] = "Sí"
                actualizar_o_crear_fila(datos_temp[ref_canal_id])

    else:
        if canal_id not in PARTNERS_CHANNELS:
            # Ignorar si canal no está en la lista de partners
            return

        hoja_nombre = PARTNERS_CHANNELS[canal_id]
        sheet = client_gs.open(GOOGLE_SHEET_NAME).worksheet(hoja_nombre)

        if canal_id not in datos_temp:
            datos_temp[canal_id] = {
                "partner": autor,
                "ultima": "",
                "proxima": "",
                "estado": "Pendiente",
                "plantilla": "No",
                "everyone": "No",
                "timestamp": "No",
                "mencion": "No"
            }

        if es_plantilla(contenido):
            datos_temp[canal_id]["plantilla"] = "Sí"
            datos_temp[canal_id]["ultima"] = fecha_hoy

        if "@everyone" in contenido or any(str(role) == "@everyone" for role in message.role_mentions):
            datos_temp[canal_id]["everyone"] = "Sí"

        if "<t:" in contenido:
            fecha_ts = extraer_timestamp(contenido)
            if fecha_ts:
                datos_temp[canal_id]["timestamp"] = "Sí"
                datos_temp[canal_id]["proxima"] = fecha_ts

        actualizar_o_crear_fila(datos_temp[canal_id])

    await bot.process_commands(message)

# ------------------ KEEP ALIVE WEB SERVER ------------------

async def handle_ping(request):
    return web.Response(text="Bot is alive!")

app = web.Application()
app.router.add_get("/", handle_ping)

async def start_webserver():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()

# ------------------ SELF PING TASK ------------------

@tasks.loop(minutes=5)
async def self_ping():
    url = "https://discord-bot-partners-tc.onrender.com/"
    try:
        async with aiohttp.ClientSession() as session:
            await session.get(url)
    except Exception as e:
        print(f"Error en self-ping: {e}")

print("DISCORD_BOT_TOKEN:", os.getenv("DISCORD_BOT_TOKEN"))

# ------------------ ARRANQUE BOT + WEB ------------------

async def main():
    await start_webserver()
    self_ping.start()
    await bot.start(os.getenv("DISCORD_BOT_TOKEN"))

asyncio.run(main())

bot.run(os.getenv("DISCORD_BOT_TOKEN"))
