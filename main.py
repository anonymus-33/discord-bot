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
CANAL_CONTROL_ID = 1389679233214845030
CANAL_BLOX_ID = 1389679151853732003
GOOGLE_SHEET_NAME = "Control rendimiento TC"
CRED_FILE = "cred.json"

# ------------ GOOGLE SHEETS SETUP ------------
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/spreadsheets",
         "https://www.googleapis.com/auth/drive.file",
         "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, scope)
client_gs = gspread.authorize(creds)
sheet = client_gs.open(GOOGLE_SHEET_NAME).worksheet("blox-fruits")

# ------------ DISCORD BOT SETUP ------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------ FUNCIONES AUXILIARES ------------

def buscar_fila(partner):
    registros = sheet.get_all_records()
    for i, row in enumerate(registros, start=2):
        if row.get("Persona") == partner:
            return i
    return None

def actualizar_o_crear_fila(datos):
    fila_existente = buscar_fila(datos["partner"])

    # Determinar estado
    if all(datos[key] == "Sí" for key in ["plantilla", "everyone", "timestamp", "mencion"]):
        estado = "RENOVADO"
    else:
        estado = "PENDIENTE"

    fila_data = [
        datos["partner"],      # Persona (col A)
        datos["ultima"],       # Última renovación (col B)
        datos["proxima"],      # Próxima renovación (col C)
        estado,                # Estado de renovación (col D)
        datos["plantilla"],    # Ha Enviado Plantilla (col E)
        datos["everyone"],     # Ping @everyone (col F)
        datos["timestamp"],    # Timestamp correcto (col G)
        datos["mencion"]       # Me ha mencionado (col H)
    ]

    if fila_existente:
        sheet.update(f"A{fila_existente}:H{fila_existente}", [fila_data])
        # Aplicar formato SOLO en la columna D
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
        # Limpiar formato en la columna E (Ha enviado plantilla)
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

        if canal_id == CANAL_BLOX_ID:
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
    try:
        async with aiohttp.ClientSession() as session:
            await session.get("http://localhost:8080/")
    except Exception as e:
        print(f"Error en self-ping: {e}")

# ------------------ ARRANQUE BOT + WEB ------------------

async def main():
    await start_webserver()
    self_ping.start()
    await bot.start(os.getenv("DISCORD_BOT_TOKEN"))

asyncio.run(main())

bot.run(os.getenv("DISCORD_BOT_TOKEN"))
