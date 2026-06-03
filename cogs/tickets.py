import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncpg
import asyncio
import os
import json
import google.generativeai as genai
from google.api_core.exceptions import GoogleAPIError
import re
import random # Agregado para la rotación de API Keys

# --- CONFIGURACIÓN DE NEGOCIO AND ACCESO HIGH-LEVEL ---
ID_CATEGORIA_SUGERENCIAS = 1510095191275737088
ID_CANAL_PROMO_TEST = 1503937966748205056
FABRIZIO_ID = 704501115110162542

# Configuración de Roles y Precios estricta
ROLES = {
    "Diamante": {"id": 1423389000122499083, "ars": 4100, "usd": 4.0},
    "Oro": {"id": 1423389001984905248, "ars": 3700, "usd": 3.5},
    "Plata": {"id": 1478203883661820106, "ars": 2100, "usd": 2.0}
}

# Diccionario de respuestas predefinidas para optimizar uso de API
RESPUESTAS_PREDEFINIDAS = {
    "hola bot": "¡Hola! Soy el asistente automatizado. Si tenés un comprobante, subilo. Si tenés dudas, preguntá.",
    "hola": "¡Hola! Soy el asistente automatizado. Si tenés un comprobante, subilo. Si tenés dudas, preguntá.",
    "entrega?": "La entrega es inmediata tras la validación automática de tu comprobante de pago.",
    "gracias bot": "¡De nada! Aquí estaré si necesitas algo más.",
    "gracias": "¡De nada! Aquí estaré si necesitas algo más.",
    "adios bot": "¡Hasta luego! El ticket se cerrará automáticamente en 24hs si no hay más actividad.",
    "adios": "¡Hasta luego! El ticket se cerrará automáticamente en 24hs si no hay más actividad."
}

class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.mi_id = FABRIZIO_ID # Tu ID de usuario para detección de menciones
        
        # --- ARQUITECTURA DEFENSIVA: Pool secuencial de 5 modelos funcionales validados ---
        self.model_pool = [
            "gemini-3.5-flash", 
            "gemini-3.1-flash-lite", 
            "gemini-2.5-flash", 
            "gemini-2.5-flash-lite", 
            "gemini-flash-latest"
        ]
        
        # Setup Multi-API Key para evitar saturación
        self.api_keys = [os.environ.get(k) for k in os.environ.keys() if k.startswith("GEMINI_API_KEY") and os.environ.get(k)]
        self.current_key_index = 0 # Índice fijo para controlar la conmutación secuencial de cuotas
        
        if not self.api_keys:
            print("⚠️ [ADVERTENCIA] No se encontraron variables GEMINI_API_KEY en .env o Render.")
        else:
            print(f"✅ BÚNKER DE IA: {len(self.api_keys)} API Keys y pool de {len(self.model_pool)} modelos listos para conmutación.")

    async def cog_load(self):
        # Crear la tabla de tickets en NeonDB asegurando el uso de BIGINT para IDs
        query_tickets = """
            CREATE TABLE IF NOT EXISTS tickets (
                channel_id BIGINT PRIMARY KEY,
                user_id BIGINT,
                estado TEXT DEFAULT 'abierto',
                ultimo_mensaje TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                hablo BOOLEAN DEFAULT FALSE
            )
        """
        # Crear tabla de pagos
        query_pagos = """
            CREATE TABLE IF NOT EXISTS pagos (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                monto NUMERIC,
                moneda TEXT,
                rol TEXT,
                fecha TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """
        try:
            async with self.bot.pool.acquire(timeout=10.0) as conn:
                await conn.execute(query_tickets)
                await conn.execute(query_pagos)
                # Añadimos la columna 'hablo' si es que la tabla ya existía de antes
                try:
                    await conn.execute("ALTER TABLE tickets ADD COLUMN hablo BOOLEAN DEFAULT FALSE")
                except asyncpg.PostgresError:
                    pass # Si tira error es porque la columna ya existe, no pasa nada
            print("✅ [Tickets & Pagos] Tablas verificadas/creadas exitosamente.")
        except Exception as e:
            print(f"❌ [DB Error] Error al crear las tablas: {e}")
            
        # Iniciar la limpieza periódica y el remarketing automático
        self.cleanup_tickets.start()
        self.auto_promo_refresh.start()

    async def cog_unload(self):
        self.cleanup_tickets.cancel()
        self.auto_promo_refresh.cancel()

    # --- OMNI-PROTOCOLO SELECCIÓN Y ROTACIÓN HÍBRIDA DE CAPA GRATUITA ---
    async def _generate_content_with_rotation(self, prompt, image_parts=None):
        """Ejecuta la conmutación de API Keys y salta secuencialmente por el pool de 5 modelos ante errores."""
        if not self.api_keys:
            raise Exception("No se encontraron API Keys configuradas en el entorno.")
        
        # El sistema probará un ciclo entero por cada API Key disponible en tu pool
        for intento_key in range(len(self.api_keys)):
            llave_actual = self.api_keys[self.current_key_index]
            genai.configure(api_key=llave_actual)
            
            # Recorremos de forma secuencial el pool de 5 modelos validados para la llave activa
            for model_name in self.model_pool:
                try:
                    model = genai.GenerativeModel(model_name)
                    
                    if image_parts:
                        response = await asyncio.wait_for(
                            asyncio.to_thread(model.generate_content, contents=[prompt, image_parts[0]]),
                            timeout=30.0
                        )
                    else:
                        response = await asyncio.wait_for(
                            asyncio.to_thread(model.generate_content, contents=[prompt]),
                            timeout=30.0
                        )
                    return response.text.strip()
                    
                except Exception as e:
                    # Captura la excepción específica de la API sin romper la ejecución del bot de soporte
                    print(f"⚠️ [Advertencia IA] Fallo con modelo {model_name} usando API Key índice {self.current_key_index}: {e}. Rotando al siguiente modelo funcional...")
                    continue # Salta al siguiente modelo disponible del pool de 5
            
            # Si el bucle de modelos termina sin retornar, significa que los 5 modelos fallaron de corrido para esta Key
            indice_viejo = self.current_key_index
            self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
            print(f"🔄 [CONMUTACIÓN CRÍTICA] Pool de 5 modelos agotado para API Key índice {indice_viejo}. Saltando a API Key índice {self.current_key_index} para reiniciar ciclo entero...")
            
        # Si salimos de ambos bucles, las dos llaves consumieron sus cuotas diarias o colapsaron por completo
        raise Exception("🚨 [EXCEPCIÓN CRÍTICA FINAL] Ambas API Keys agotaron de manera consecutiva el pool de 5 modelos funcionales.")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        """Lanza el mensaje de presentación suavizado y el Embed adaptado según la categoría del ticket."""
        if isinstance(channel, discord.TextChannel) and (channel.name.startswith("ticket-") or channel.name.startswith("sug-") or channel.category_id == ID_CATEGORIA_SUGERENCIAS):
            # Delay de seguridad de 10 segundos
            await asyncio.sleep(10.0)
            
            channel = channel.guild.get_channel(channel.id)

            # --- REGISTRO INICIAL EN DB ---
            # --- REGISTRO INICIAL EN DB (GUARDADO INCONDICIONAL) ---
            user_id = 0 # Valor 0 por defecto por si Ticket Tool tiene lag
            if channel:
                for target in channel.overwrites:
                    if isinstance(target, discord.Member) and not target.bot:
                        user_id = target.id
                        break
            
            try:
                async with self.bot.pool.acquire(timeout=5.0) as conn:
                    # 1. Guardamos al usuario solo si lo detectamos a tiempo
                    if user_id != 0:
                        await conn.execute("INSERT INTO usuarios (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                    
                    # 2. GUARDAMOS EL TICKET SÍ O SÍ (Incluso si user_id es 0)
                    await conn.execute("""
                        INSERT INTO tickets (channel_id, user_id, estado, ultimo_mensaje, hablo)
                        VALUES ($1, $2, 'abierto', CURRENT_TIMESTAMP, FALSE)
                        ON CONFLICT (channel_id) DO NOTHING
                    """, channel.id, user_id)
            except Exception as e:
                print(f"❌ [DB Error] No se pudo registrar ticket inicial {channel.id}: {e}")
            # ------------------------------
            # ------------------------------
            
            # --- DETECCIÓN E INYECCIÓN DE EMBED BASADO EN CATEGORÍA ---
            if channel and (channel.category_id == ID_CATEGORIA_SUGERENCIAS or channel.name.startswith("sug-")):
                embed = discord.Embed(
                    title="💎 Petición Única de Modelos", 
                    description="Elegí la chica que querés incorporar al catálogo de forma 100% privada.", 
                    color=0x1DB954 # Color Verde Premium Estilo Spotify
                )
                embed.add_field(
                    name="💰 COSTO FIJO DE LA SOLICITUD", 
                    value="🇦🇷 Argentina: $2.000 ARS\n🌍 Internacional: $2 USD", 
                    inline=False
                )
                embed.add_field(name="Alias (ARS):", value="LENGUA.LUJOSA.TELAR", inline=False)
                embed.add_field(name="CBU (ARS):", value="3840200500000026286680", inline=False)
                embed.add_field(
                    name="🌍 DOLARES (PayPal) Enviar monto exacto a este correo:", 
                    value="sesarjavier28@gmail.com", 
                    inline=False
                )
                embed.add_field(
                    name="✅ ¿Cómo proceder? Seguí estos pasos:", 
                    value="1. Envía la foto o PDF del comprobante de pago de la sugerencia.\n2. Enviá una **red social o URL de la modelo** (Instagram, Twitter, OnlyFans, TikTok, etc.) para procesar la búsqueda.\n3. El bot validará el monto y **Tito Calderón** se encargará de procesar e incorporar el canal.\n\n*🛡️ Garantía Absoluta*: Si el contenido solicitado no se encuentra disponible, **se te devuelve el dinero de inmediato**.", 
                    inline=False
                )
                embed.add_field(
                    name="⚠️ INFORMACION DE INFRAESTRUCTURA (CUPOS LIMITADOS)",
                    value="Discord prohíbe tener más de 500 canales en total por servidor. Actualmente estamos cerca de ese numero, lo que nos deja un margen operativo ajustado. Por este motivo, **el precio de las peticiones irá aumentando progresivamente** a medida que se completen los canales para regular el almacenamiento.",
                    inline=False
                )
                bienvenida = (
                    "¡Hola! Soy tu asistente automatizado para peticiones exclusivas. 🤖\n"
                    "Estoy acá para darte soporte y procesar tu solicitud bajo estricto anonimato.\n\n"
                )
            else:
                embed = discord.Embed(
                    title="🛒 Zona de Compras", 
                    description="Elegí tu rango y mirá los datos de pago abajo.", 
                    color=0x2B2D31 # Color oscuro de Discord
                )
                embed.add_field(
                    name="📉 LISTA DE PRECIOS", 
                    value="💎 Rango Diamante: 🇦🇷 Argentina: $4.100 ARS 🌍 Internacional: $4 USD\n🥇 Rango Oro: 🇦🇷 Argentina: $3.700 ARS 🌍 Internacional: $3,5 USD\n🥈 Rango Plata: 🇦🇷 Argentina: $2.100 ARS 🌍 Internacional: $2 USD", 
                    inline=False
                )
                embed.add_field(name="Alias:", value="LENGUA.LUJOSA.TELAR", inline=False)
                embed.add_field(name="CBU:", value="3840200500000026286680", inline=False)
                embed.add_field(
                    name="🌍 DOLARES (PayPal) Enviar monto exacto a este correo:", 
                    value="sesarjavier28@gmail.com\n", 
                    inline=False
                )
                embed.add_field(
                    name="✅ ¿Ya pagaste? Seguí estos pasos:", 
                    value="1. Aclara que rango o rangos estas comprando\n2. Envia el comprobante (Foto o PDF)\n3. El bot entrega el rol correspondiente al instante\n\n❓ Si tenes dudas o problemas, etiqueta a @titocalderon y espera a recibir ayuda\n\n*(Si querés 2 o los 3 rangos juntos, podés transferir el total correspondiente según la combinación elegida.)*", 
                    inline=False
                )
                bienvenida = (
                    "¡Hola! Soy tu asistente de ventas automatizado. 🤖\n"
                    "Estoy aquí para ayudarte a obtener tu rango de forma rápida.\n\n"
                )

            try:
                if channel:
                    await channel.send(content=bienvenida, embed=embed)
            except Exception as e:
                print(f"❌ [Error] No se pudo enviar bienvenida en {channel.name}: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Automatización: Elimina los tickets si el usuario abandona el servidor."""
        user_id = member.id
        query = "SELECT channel_id FROM tickets WHERE user_id = $1"
        try:
            async with self.bot.pool.acquire(timeout=5.0) as conn:
                records = await conn.fetch(query, user_id)
                for record in records:
                    channel_id = record['channel_id']
                    channel = member.guild.get_channel(channel_id)
                    if channel:
                        try:
                            await channel.delete(reason=f"Auto-Close: Usuario {member.name} abandonó el servidor.")
                            print(f"🗑️ [Auto-Close] Ticket {channel_id} borrado porque el usuario se fue.")
                        except discord.NotFound:
                            pass
                        except Exception as e:
                            print(f"❌ [Error] al borrar ticket {channel_id}: {e}")
                
                # Limpiar registro en la base de datos
                await conn.execute("DELETE FROM tickets WHERE user_id = $1", user_id)
        except Exception as e:
            print(f"❌ [DB Error] en on_member_remove: {e}")

    @app_commands.command(name="manual", description="Apaga la IA en este ticket para hablar manualmente con el cliente")
    @app_commands.default_permissions(administrator=True) # Exclusivo para admins
    async def modo_manual(self, interaction: discord.Interaction):
        if not hasattr(interaction.channel, 'name') or (not interaction.channel.name.startswith("ticket-") and not interaction.channel.name.startswith("sug-")):
            await interaction.response.send_message("⚠️ Este comando solo se puede usar dentro de un ticket.", ephemeral=True)
            return

        try:
            async with self.bot.pool.acquire(timeout=5.0) as conn:
                await conn.execute("UPDATE tickets SET estado = 'pausado' WHERE channel_id = $1", interaction.channel.id)
            await interaction.response.send_message("🛑 **Modo Manual Activado.**\nLa IA ha sido apagada en este ticket. Podés hablar con el cliente tranquilamente sin que el bot intervenga.")
        except Exception as e:
            print(f"❌ [DB Error] al pausar ticket {interaction.channel.id}: {e}")
            await interaction.response.send_message("❌ Hubo un error al intentar pausar la IA.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.content.startswith('/'):
            return

        # Escudo de escucha flexible: Soporta tanto canales ticket- como sug- y filtros de categoría
        es_canal_valido = hasattr(message.channel, 'name') and (
            message.channel.name.startswith("ticket-") or 
            message.channel.name.startswith("sug-") or 
            getattr(message.channel, 'category_id', None) == ID_CATEGORIA_SUGERENCIAS
        )
        if not es_canal_valido:
            return

        # Verificamos estado manual
        try:
            async with self.bot.pool.acquire(timeout=5.0) as conn:
                estado_ticket = await conn.fetchval("SELECT estado FROM tickets WHERE channel_id = $1", message.channel.id)
                if estado_ticket == 'pausado':
                    return 
        except Exception:
            pass

        # --- LÓGICA DE MENCIONES ---
        mencion_admin = f"<@{self.mi_id}>" in message.content or f"<@!{self.mi_id}>" in message.content
        if self.bot.user.mentioned_in(message) or mencion_admin:
            await message.reply("¡Hola! Estoy acá para darte soporte inmediato. ¿Qué duda o inconveniente tenés?")
            return

        # --- ESCUDO ANTI-SPAM GLOBAL ---
        patron_invitacion = r"(discord\.(gg|io|me|li|com\/invite)|discordapp\.com\/invite)\/([a-zA-Z0-9\-]+)"
        if re.search(patron_invitacion, message.content, re.IGNORECASE):
            await message.delete()
            if message.author.id != FABRIZIO_ID:
                try:
                    await message.author.ban(reason="Spam de invitaciones detectado en ticket.")
                    await message.channel.send(f"🚨 **Sistema de Seguridad**: Usuario {message.author.name} baneado permanentemente por spam de invitaciones.")
                    print(f"🔨 [BAN] {message.author.name} ({message.author.id}) por link de invitación.")
                    return 
                except discord.Forbidden:
                    await message.channel.send("⚠️ No tengo permisos para banear a este usuario, pero borré el link.")
                    return

        # Registrar actividad y actualizar timestamp
        await self._update_ticket_activity(message)

        # Intercepción asincrónica de comprobantes (Foto o PDF)
        has_valid_attachment = any(att.content_type and (att.content_type.startswith('image/') or att.content_type == 'application/pdf') for att in message.attachments)
        if message.attachments and has_valid_attachment:
            await self.handle_receipt_image(message)
            return

        # Respuestas rápidas locales
        contenido_limpio = message.content.strip().lower()
        if contenido_limpio in RESPUESTAS_PREDEFINIDAS:
            await message.reply(RESPUESTAS_PREDEFINIDAS[contenido_limpio])
            return

        # Flujo continuo del Chatbot conversacional
        await self.handle_support_query(message)

    async def _update_ticket_activity(self, message: discord.Message):
        channel_id = message.channel.id
        user_id = message.author.id
        try:
            async with self.bot.pool.acquire(timeout=5.0) as conn:
                await conn.execute("INSERT INTO usuarios (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                query = """
                    INSERT INTO tickets (channel_id, user_id, estado, ultimo_mensaje, hablo)
                    VALUES ($1, $2, 'abierto', CURRENT_TIMESTAMP, TRUE)
                    ON CONFLICT (channel_id) DO UPDATE 
                    SET ultimo_mensaje = CURRENT_TIMESTAMP, hablo = TRUE, user_id = EXCLUDED.user_id
                """
                await conn.execute(query, channel_id, user_id)
        except Exception as e:
            print(f"❌ [DB Error] No se pudo actualizar actividad de ticket {channel_id}: {e}")

    async def handle_receipt_image(self, message: discord.Message):
        attachment = next((a for a in message.attachments if a.content_type.startswith('image/') or a.content_type == 'application/pdf'), None)
        if not attachment:
            return
            
        advertencia = await message.channel.send("⏳ **Auditoría IA**: Analizando comprobante de pago...")
        es_sugerencia = (getattr(message.channel, 'category_id', None) == ID_CATEGORIA_SUGERENCIAS) or message.channel.name.startswith("sug-")

        try:
            image_data = await attachment.read()
            image_parts = [{"mime_type": attachment.content_type, "data": image_data}]

            historial = []
            async for msg in message.channel.history(limit=5, before=message):
                if not msg.author.bot:
                    historial.append(f"{msg.author.name}: {msg.content}")
            
            contexto = "\n".join(historial)
            
            if es_sugerencia:
                prompt = f"""
Contexto reciente del chat:
{contexto}

SOS UN AUDITOR FINANCIERO ESTRICTO. Analizá esta imagen o PDF para validar si es un comprobante de transferencia COMPLETADO (ej: Mercado Pago, Banco, PayPal) para la PETICIÓN ÚNICA DE MODELO de Tito Calderón.
REGLA CRÍTICA Y ESTRICTA: Devues verificar OBLIGATORIAMENTE que el destinatario de la transferencia sea 'Fabrizio Giovanni Cocca Ducay' (o Fabrizio Cocca), O que el correo destinatario sea 'sesarjavier28@gmail.com' (para el caso de PayPal). Si es otra persona, marca "es_comprobante": false.
REGLA 1: Buscá evidencia de que el pago finalizó (ej: "Transferencia exitosa", "Pago realizado").
REGLA 2: El costo de la sugerencia es exactamente $2000 ARS o $2 USD. Si el monto coincide o supera este valor, marca "valido": true. Caso contrario, marca "valido": false.

Devolve ÚNICAMENTE un objeto JSON válido con la siguiente estructura (NO uses markdown ni comillas invertidas):
{{
  "es_comprobante": true_o_false,
  "monto": float,
  "moneda": "ARS_o_USD",
  "rol_detectado": "Sugerencia",
  "valido": true_o_false,
  "diferencia": float,
  "necesita_preguntar": false
}}
"""
            else:
                prompt = f"""
Contexto reciente del chat (puede contener el rol deseado o aclarar montos parciales):
{contexto}

SOS UN AUDITOR FINANCIERO ESTRICTO. Analizá esta imagen o PDF para validar si es un comprobante de transferencia COMPLETADO (ej: Mercado Pago, Banco, PayPal) para la auditoría de Tito Calderón.
REGLA CRÍTICA Y ESTRICTA: Debes verificar OBLIGATORIAMENTE que el destinatario de la transferencia sea 'Fabrizio Giovanni Cocca Ducay' (o Fabrizio Cocca), O que el correo destinatario sea 'sesarjavier28@gmail.com' (para el caso de PayPal). Si logras leer el nombre o correo del destinatario y es otra persona (por ejemplo, le están transfiriendo a un amigo u otro nombre), marca "es_comprobante": false.
REGLA 1: Buscá evidencia de que el pago finalizó (ej: "Transferencia exitosa", "Pago realizado"). Ignorá capturas de 'pre-transferencia' o pantallas de confirmación sin ejecutar.
REGLA 2: Si el formato numérico usa coma para miles (ej 4,100.00), convertilo a un número limpio (4100).
REGLA 3 DE MONTO RANDOM: 
- Si el monto NO coincide exactamente con un rango o combo, pero el usuario ESPECIFICÓ uno en el contexto, validalo contra ese.
- Si el monto es random y el usuario NO especificó qué quería, devolvé 'necesita_preguntar': true.
REGLA 4 (PRECIOS EXACTOS Y COMBOS): Compará el monto pagado con nuestros precios estrictos:
- Diamante: $4100 ARS / $4 USD
- Oro: $3700 ARS / $3.5 USD
- Plata: $2100 ARS / $2 USD
- Diamante + Oro: $7800 ARS / $7.5 USD
- Diamante + Plata: $6200 ARS / $6 USD
- Oro + Plata: $5800 ARS / $5.5 USD
- LOS 3 RANGOS JUNTOS (Todos): $9900 ARS / $9.5 USD
REGLA 5: TUS DATOS DE COBRO (NUNCA INVENTES OTROS):
- Alias: LENGUA.LUJOSA.TELAR
- CBU: 3840200500000026286680
- PayPal (USD): sesarjavier28@gmail.com
- Binance Pay ID (USDT): 552346130
- Titular: Fabrizio Giovanni Cocca Ducay (o Fabrizio Cocca)
REGLA 6: RESPUESTAS CORTAS: Respondé SIEMPRE a lo que se te pregunta y pide. Sé directo, servicial y al grano. No uses lenguaje robótico ni des discursos largos.
REGLA 7: DURACIÓN DE LOS RANGOS: Los rangos son PERMANENTES y de por vida. NUNCA expiran ni requieren renovación.
REGLA 8 (FORMATO DE COMBINACIONES): En el JSON, para la key "rol_detectado", si el usuario pagó por un combo, DEBES separar los roles estrictamente por comas (Ejemplo: "Diamante, Oro", "Diamante, Plata", "Oro, Plata" o "Todos"). NUNCA uses "y" ni "+". Si es uno solo, pones el nombre solo.

Devolve ÚNICAMENTE un objeto JSON válido con la siguiente estructura (NO uses markdown ni comillas invertidas):
{{
  "es_comprobante": true_o_false,
  "monto": float,
  "moneda": "ARS_o_USD",
  "rol_detectado": "Diamante, Oro, Plata, Todos, o combinaciones con coma",
  "valido": true_o_false,
  "diferencia": float,
  "necesita_preguntar": true_o_false
}}
"""

            # --- INTERCEPCIÓN PROTOCOLO DE CONMUTACIÓN DE LLAVES Y MODELOS ---
            text = await self._generate_content_with_rotation(prompt, image_parts)
            
            text = text.strip()
            if text.startswith("```json"):
                text = text[7:-3].strip()
            elif text.startswith("```"):
                text = text[3:-3].strip()
                
            datos = json.loads(text)

            if not datos.get("es_comprobante"):
                await advertencia.edit(content="❌ **Auditoría Fallida**: No es válido, puede ser un fallo del bot, enviá la foto porfa y ahí vemos qué pasó.")
                return

            if datos.get("necesita_preguntar"):
                await advertencia.edit(content=f"🤔 **Monto no reconocido**: Detectamos un pago de **{datos.get('monto')} {datos.get('moneda')}**, pero no coincide con ningún rango exacto y no especificaste cuál querías en el chat. ¿Podés aclarar qué rangos estás comprando?")
                return

            rol = datos.get("rol_detectado")
            valido = datos.get("valido", False)
            diferencia = float(datos.get("diferencia", 0.0))

            # --- GESTIÓN DE AUDITORÍA EN CANAL DE SUGERENCIAS ---
            if es_sugerencia:
                if not valido:
                    await advertencia.edit(content=f"⚠️ **Comprobante Insuficiente**\nEl pago detectado de **{datos['monto']} {datos['moneda']}** no es suficiente para procesar la petición.\nEl costo fijo es de $2000 ARS / $2 USD. Por favor, abona el resto y envía el comprobante completo.")
                else:
                    msg_exito = f"✅ **¡Pago de Petición Verificado con Éxito!**\nEl bot detectó e impactó un pago correcto de **{datos['monto']} {datos['moneda']}**.\n\n🔔 <@{FABRIZIO_ID}> ¡Petición de canal recibida! Vení al ticket a ver qué red social de la modelo envió el usuario."
                    await advertencia.edit(content=msg_exito)
                    await self._marcar_ticket_completado(message.channel.id)

                    try:
                        async with self.bot.pool.acquire(timeout=5.0) as conn:
                            await conn.execute(
                                "INSERT INTO pagos (user_id, monto, moneda, rol) VALUES ($1, $2, $3, $4)",
                                message.author.id, float(datos.get('monto', 0)), datos.get('moneda', 'ARS'), "Petición Chica"
                            )
                    except Exception as e:
                        print(f"❌ [DB Error] No se pudo registrar el pago en la tabla 'pagos': {e}")
                return

            # --- GESTIÓN DE AUDITORÍA EN CANAL DE RANGOS ---
            if not rol or (rol not in ROLES and rol != "Todos" and "," not in rol):
                await advertencia.edit(content=f"⚠️ **Atención**: Comprobante de {datos.get('monto', 0)} {datos.get('moneda', '')} verificado, pero no alcanza o no concuerda para un rol específico, aclara que rangos estas comprando o <@704501115110162542> revisalo manualmente.")
                return

            if not valido:
                faltante = abs(diferencia)
                await advertencia.edit(content=f"⚠️ **Comprobante Insuficiente**\nEl pago detectado de **{datos['monto']} {datos['moneda']}** no es suficiente para el rol solicitado.\nFaltan **{faltante} {datos['moneda']}**. Por favor, abona el resto y envía el nuevo comprobante.")
                return

            try:
                roles_a_dar = []
                if rol == "Todos":
                    roles_a_dar = [message.guild.get_role(r["id"]) for r in ROLES.values() if message.guild.get_role(r["id"])]
                elif "," in rol:
                    roles_split = rol.split(",")
                    for rs in roles_split:
                        rs = rs.strip()
                        if rs in ROLES:
                            role_obj = message.guild.get_role(ROLES[rs]["id"])
                            if role_obj: roles_a_dar.append(role_obj)
                else:
                    role_id = ROLES[rol]["id"]
                    role_obj = message.guild.get_role(role_id)
                    if role_obj: roles_a_dar.append(role_obj)
                
                if not roles_a_dar:
                    await advertencia.edit(content=f"❌ **Error de Sistema**: El rol no existe o no está configurado correctamente en el servidor. Avisa a un administrador.")
                    return

                await message.author.add_roles(*roles_a_dar, reason="Aprobado por Auditoría IA")
                
                msg_exito = f"✅ **¡Pago Verificado con Éxito!**\nSe te ha otorgado el rol **{rol}** automáticamente.\n\n🔔 <@704501115110162542> auditoría automática completada."
                await advertencia.edit(content=msg_exito)
                await self._marcar_ticket_completado(message.channel.id)

                try:
                    async with self.bot.pool.acquire(timeout=5.0) as conn:
                        monto_num = float(datos.get('monto', 0))
                        moneda_str = datos.get('moneda', 'ARS')
                        await conn.execute(
                            "INSERT INTO pagos (user_id, monto, moneda, rol) VALUES ($1, $2, $3, $4)",
                            message.author.id, monto_num, moneda_str, rol
                        )
                except Exception as e:
                    print(f"❌ [DB Error] No se pudo registrar el pago en la tabla 'pagos': {e}")
                
            except discord.Forbidden:
                await advertencia.edit(content="❌ **Error de Permisos**: No tengo los permisos de jerarquía necesarios para asignar el rol.")

        except asyncio.TimeoutError:
            await advertencia.edit(content="⏳ **Servidores saturados**: ¡Recibimos tu comprobante! Pero los servidores de Google están bajo mucha carga ahora mismo. **No es un error de tu pago**. Por favor, volvé a subir la foto en 1 o 2 minutos para que el bot pueda procesarla.")
        except json.JSONDecodeError:
            print(f"❌ [IA JSON Error] Texto recibido: {text}")
            await advertencia.edit(content="❌ **Error de Procesamiento**: La IA dio una respuesta ilegible. Espera a un administrador.")
        except GoogleAPIError as e:
            print(f"❌ [Google API Error]: {e}")
            await advertencia.edit(content="⚠️ **Servicio Interrumpido**: Problemas al contactar a la IA (Límite de cuota o servicio caído).")
        except Exception as e:
            print(f"❌ [IA Error Inesperado]: {e}")
            await advertencia.edit(content="❌ Ocurrió un error inesperado al procesar el comprobante.")

    async def _marcar_ticket_completado(self, channel_id: int):
        query = "UPDATE tickets SET estado = 'completado' WHERE channel_id = $1"
        try:
            async with self.bot.pool.acquire(timeout=5.0) as conn:
                await conn.execute(query, channel_id)
        except Exception as e:
            print(f"❌ [DB Error] Fallo al marcar ticket como completado: {e}")

    async def handle_support_query(self, message: discord.Message):
        # Recopilamos historial para dar contexto a la IA y permitir que entienda aclaraciones posteriores
        historial = []
        async for msg in message.channel.history(limit=10, before=message):
            autor = "Usuario" if not msg.author.bot else "Bot"
            historial.append(f"{autor}: {msg.content}")
        
        contexto_previo = "\n".join(historial)
        es_sugerencia = (getattr(message.channel, 'category_id', None) == ID_CATEGORIA_SUGERENCIAS) or message.channel.name.startswith("sug-")

        # --- SEPARACIÓN ESTRICTA DE CONTEXTOS PARA EL CHATBOT ---
        if es_sugerencia:
            prompt = f"""
Actúa como un asistente virtual premium de Discord para atender la zona de PETICIONES ÚNICAS DE MODELOS de Tito Calderón.
HISTORIAL DE CONVERSACIÓN RECIENTE:
{contexto_previo}

TUS DATOS DE COBRO ESTRICTOS:
- Alias (MercadoPago/Bancos ARS): LENGUA.LUJOSA.TELAR
- CBU: 3840200500000026286680
- PayPal (USD): sesarjavier28@gmail.com
- Titular de la cuenta bancaria: Fabrizio Giovanni Cocca Ducay (Tito Calderón es solo el nombre de la comunidad).

REGLAS DE NEGOCIO Y RESPUESTA (ESTRICTAS):
1. PRECIO FIJO: Cada petición cuesta $2000 ARS o $2 USD.
2. RESPUESTAS CORTAS: Máximo 1 o 2 párrafos cortos (no más de 60 palabras). Sé directo y al grano.
3. IDENTIDAD BANCARIA: Si preguntan el nombre del titular o a quién transferir, es Fabrizio Giovanni Cocca Ducay.
4. EXIGIR RED SOCIAL: El usuario DEBE mandar una red social o URL de la modelo.
5. ASINCRONÍA DE FOTOS: Si el usuario dice "ahí pasé la foto", "ya pagué" o "mandé el comprobante", responde: "¡Perfecto! El sistema de auditoría lo está analizando en este preciso momento." NO vuelvas a pedir la foto.
6. ESTADO POST-VENTA (MEMORIA): Si en el HISTORIAL ves que el bot ya dijo "Pago de Petición Verificado con Éxito", tu objetivo cambió. NO pidas comprobantes ni sigas vendiendo. Agradecele y decile que Tito Calderón ya está procesando su pedido.
7. PRIVACIDAD ABSOLUTA: El proceso es privado y anónimo.
8. ZERO TRUST: No des nada por válido solo con palabras si no ves la validación en el historial.

Consulta actual del usuario: "{message.content}"
"""
        else:
            prompt = f"""
Actúa como un asistente de ventas de Discord para ayudar al usuario a realizar su pago y cerrar la compra de rangos de acceso del servidor de Tito Calderón.
HISTORIAL DE CONVERSACIÓN:
{contexto_previo}

TUS DATOS DE COBRO ESTRICTOS:
- Alias (MercadoPago/Bancos ARS): LENGUA.LUJOSA.TELAR
- CBU: 3840200500000026286680   
- PayPal (USD): sesarjavier28@gmail.com
- Binance Pay ID (USDT): 552346130
- Titular de la cuenta bancaria: Fabrizio Giovanni Cocca Ducay (o Fabrizio Cocca). Tito Calderón es solo el nombre del usuario/dueño del servidor.

REGLAS DE NEGOCIO Y RESPUESTA (ESTRICTAS):
1. PRECIOS: Diamante ($4100 ARS / $4 USD), Oro ($3700 ARS / $3.5 USD), Plata ($2100 ARS / $2 USD). Combos: Diamante+Oro ($7800 ARS / $7.5 USD), Diamante+Plata ($6200 ARS / $6 USD), Oro+Plata ($5800 ARS / $5.5 USD), Todos ($9900 ARS / $9.5 USD). NUNCA des otros precios.
2. RESPUESTAS CORTAS: Máximo 1 o 2 párrafos cortos (no más de 60 palabras). Sé directo.
3. IDENTIDAD BANCARIA: Si preguntan por el nombre del destinatario del pago o titular, es Fabrizio Giovanni Cocca Ducay.
4. ASINCRONÍA DE FOTOS: Si el usuario dice "ya lo mandé", "ahí pasé el comprobante", responde: "¡Buenísimo! El sistema automático de auditoría lo está analizando en este momento." NO le pidas que envíe la foto de nuevo.
5. ESTADO POST-VENTA (MEMORIA): Si en el HISTORIAL ves que el sistema ya validó el pago y dijo "Rol/es asignado/s" o "Pago Verificado con Éxito", TU OBJETIVO CAMBIÓ. NO vendas más ni pidas el comprobante. Dale la bienvenida al usuario, confirmale que su rol ya está activo y que disfrute del contenido.
6. SEGURIDAD CRÍTICA (ZERO TRUST): TIENES TOTALMENTE PROHIBIDO usar el comando [GRANT_ROLE] basándote únicamente en la palabra del usuario. SOLO usalo si ves en el HISTORIAL que el sistema (el bot) ya validó físicamente una imagen y pidió aclarar el rango.

INSTRUCCIÓN TÉCNICA (SOLO PARA ACLARAR RANGOS FALTANTES):
Si (y solo si) un pago previo fue validado por el sistema en el historial PERO faltó aclarar el rango que cubría ese pago, incluye al FINAL de tu respuesta este comando exacto: [GRANT_ROLE: NombreDelRol] (reemplaza NombreDelRol por Diamante, Oro o Plata. Si compró un combo, ponelos separados por coma, ej: [GRANT_ROLE: Diamante, Oro]).

Consulta actual del usuario: "{message.content}"
"""
        try:
            async with message.channel.typing():
                # --- INTERCEPCIÓN PROTOCOLO DE CONMUTACIÓN PARA EL CHATBOT ---
                respuesta_texto = await self._generate_content_with_rotation(prompt)
                await message.reply(respuesta_texto)

                # Si estamos en un canal de sugerencia, anulamos la asignación de roles automatizada
                if not es_sugerencia:
                    # Lógica avanzada para otorgar múltiples roles desde la conversación si la IA da la orden
                    ordenes_roles = re.findall(r"\[GRANT_ROLE:\s*([A-Za-z0-9_,\s]+)\]", respuesta_texto)
                    
                    if ordenes_roles:
                        roles_a_dar = []
                        nombres_roles_asignados = []
                        
                        for orden in ordenes_roles:
                            # Soporte de roles separados por comas o por tags consecutivos
                            roles_split = orden.split(",")
                            for r_name in roles_split:
                                r_name = r_name.strip()
                                if r_name in ROLES:
                                    role_obj = message.guild.get_role(ROLES[r_name]["id"])
                                    if role_obj and role_obj not in roles_a_dar:
                                        roles_a_dar.append(role_obj)
                                        nombres_roles_asignados.append(r_name)
                        
                        if roles_a_dar:
                            try:
                                # Asignación múltiple en un solo empaquetado de Discord
                                await message.author.add_roles(*roles_a_dar, reason="Aclaración de rango múltiple vía IA")
                                roles_str = ", ".join(nombres_roles_asignados)
                                await message.channel.send(f"✅ Sistema: Rol/es **{roles_str}** asignado/s tras aclaración.\n🔔 <@704501115110162542> auditoría manual/aclaración completada.")
                                await self._marcar_ticket_completado(message.channel.id)

                                # Registrar cada pago impactado en NeonDB de forma independiente
                                try:
                                    async with self.bot.pool.acquire(timeout=5.0) as conn:
                                        for r_name in nombres_roles_asignados:
                                            monto_estimado = ROLES[r_name]["ars"] 
                                            await conn.execute(
                                                "INSERT INTO pagos (user_id, monto, moneda, rol) VALUES ($1, $2, $3, $4)",
                                                message.author.id, monto_estimado, "ARS", r_name
                                            )
                                except Exception as e:
                                    print(f"❌ [DB Error] No se pudo registrar el pago aclarado en la tabla 'pagos': {e}")
                            except discord.Forbidden:
                                print(f"❌ Error de Jerarquía: El bot no tiene permisos suficientes para dar los roles: {nombres_roles_asignados}")

        except asyncio.TimeoutError:
            await message.reply("⚠️ **IA Congestionada**: Los servidores de Google están tardando en responder. Tu consulta es importante; por favor, intentá preguntar de nuevo en un instante.")
        except Exception as e:
            print(f"❌ [IA Support Error]: {e}")

    # Tarea en loop cada 30 minutos para limpieza de tickets
    @tasks.loop(minutes=30)
    async def cleanup_tickets(self):
        # Completados a las 24hs
        query_completados = """
            SELECT channel_id FROM tickets 
            WHERE estado = 'completado' 
            AND ultimo_mensaje <= CURRENT_TIMESTAMP - INTERVAL '24 hours'
        """
        query_abandonados_3h = """
            SELECT channel_id FROM tickets 
            WHERE estado = 'abierto' AND hablo = FALSE
            AND ultimo_mensaje <= CURRENT_TIMESTAMP - INTERVAL '3 hours'
        """
        # Inactivos a las 24hs (si habían hablado)
        query_abandonados_24h = """
            SELECT channel_id FROM tickets 
            WHERE estado IN ('abierto', 'pausado') AND hablo = TRUE
            AND ultimo_mensaje <= CURRENT_TIMESTAMP - INTERVAL '24 hours'
        """
        
        try:
            async with self.bot.pool.acquire(timeout=15.0) as conn:
                # 1. Limpiar completados (24hs)
                records_comp = await conn.fetch(query_completados)
                for record in records_comp:
                    channel_id = int(record['channel_id'])
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try:
                            await channel.delete(reason="Limpieza automática: 24hs tras ticket completado.")
                            print(f"🗑️ [Limpieza] Canal de ticket {channel_id} eliminado (24hs completado).")
                        except discord.Forbidden:
                            pass
                        except discord.HTTPException:
                            pass
                    await conn.execute("DELETE FROM tickets WHERE channel_id = $1", channel_id)

                # 2. Limpiar abandonados (3hs sin mensaje inicial)
                records_aban3h = await conn.fetch(query_abandonados_3h)
                for record in records_aban3h:
                    channel_id = int(record['channel_id'])
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try: 
                            await channel.delete(reason="Auto-Close: 3 horas de inactividad total desde creación.")
                            print(f"🗑️ [Auto-Close] Ticket {channel_id} borrado por 3hs sin actividad inicial.")
                        except discord.Forbidden:
                            pass
                        except discord.HTTPException:
                            pass
                    await conn.execute("DELETE FROM tickets WHERE channel_id = $1", channel_id)
                    
                # 3. Limpiar abandonados (24hs inactividad luego de hablar)
                records_aban24h = await conn.fetch(query_abandonados_24h)
                for record in records_aban24h:
                    channel_id = int(record['channel_id'])
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try: 
                            await channel.delete(reason="Auto-Close: 24 horas de inactividad después de hablar.")
                            print(f"🗑️ [Auto-Close] Ticket {channel_id} borrado por 24hs inactividad posterior.")
                        except discord.Forbidden:
                            pass
                        except discord.HTTPException:
                            pass
                    await conn.execute("DELETE FROM tickets WHERE channel_id = $1", channel_id)
                    
        except asyncpg.PostgresError as e:
            print(f"❌ [DB Error] en loop cleanup_tickets: {e}")
        except Exception as e:
            print(f"❌ [Error Inesperado] en loop cleanup_tickets: {e}")

    @cleanup_tickets.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    
    # --- 📣 LÓGICA DE REMARKETING ORGÁNICO PROGRAMADO (CANAL DE TESTEO) ---
    @tasks.loop(hours=168) # 168 horas = 7 días de ciclo estricto de recontacto
    async def auto_promo_refresh(self):
        """Tarea de Remarketing: Purgar pings anteriores del bot y disparar el recordatorio corto."""
        canal = self.bot.get_channel(ID_CANAL_PROMO_TEST)
        if canal:
            try:
                # Purga asincrónica de los mensajes viejos enviados por el bot para no acumular basura
                def is_me(m): return m.author == self.bot.user
                await canal.purge(limit=10, check=is_me)
            except Exception as e:
                print(f"⚠️ [Remarketing Error] Fallo al limpiar canal de promo: {e}")

            # Mensaje estratégico corto: Se posiciona abajo de Ticket Tool sin enterrar el botón
            bienvenida = "@everyone 🚀 **¡Actualizamos el contenido recientemente!** Abrí un ticket acá arriba y descubrí todo lo nuevo que subimos. ¡No te lo pierdas! ✨"
            try:
                await canal.send(content=bienvenida)
                print("📣 [Remarketing] Mensaje semanal de recontacto enviado con éxito.")
            except Exception as e:
                print(f"❌ Error enviando recontacto automático: {e}")

    @auto_promo_refresh.before_loop
    async def before_promo(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))