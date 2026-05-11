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
        self.mi_id = 704501115110162542 # Tu ID de usuario para detección de menciones
        
        # Setup de IA con Gemini
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)
        else:
            print("⚠️ [ADVERTENCIA] GEMINI_API_KEY no encontrada en .env")

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
            
        # Iniciar la limpieza periódica
        self.cleanup_tickets.start()

    async def cog_unload(self):
        self.cleanup_tickets.cancel()

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        """Lanza el mensaje de presentación suavizado y el Mega-Embed de precios después de un delay."""
        if isinstance(channel, discord.TextChannel) and channel.name.startswith("ticket-"):
            # Aumentamos el delay a 10 segundos para dar tiempo a que Ticket Tool asigne permisos al usuario
            await asyncio.sleep(10.0)
            
            channel = channel.guild.get_channel(channel.id)

            # --- REGISTRO INICIAL EN DB ---
            # Buscamos al dueño del ticket en los permisos del canal para empezar el reloj de 3hs
            user_id = None
            if channel:
                for target in channel.overwrites:
                    if isinstance(target, discord.Member) and not target.bot:
                        user_id = target.id
                        break
            
            if user_id:
                try:
                    async with self.bot.pool.acquire(timeout=5.0) as conn:
                        await conn.execute("INSERT INTO usuarios (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                        await conn.execute("""
                            INSERT INTO tickets (channel_id, user_id, estado, ultimo_mensaje, hablo)
                            VALUES ($1, $2, 'abierto', CURRENT_TIMESTAMP, FALSE)
                            ON CONFLICT (channel_id) DO NOTHING
                        """, channel.id, user_id)
                except Exception as e:
                    print(f"❌ [DB Error] No se pudo registrar ticket inicial {channel.id}: {e}")
            # ------------------------------
            
            # Recreando el Embed Premium de Ticket Tool
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
            embed.add_field(name="CVU:", value="0000168300000013531308", inline=False)
            embed.add_field(
                name="🌍 DOLARES (PayPal) Enviar monto exacto a este correo:", 
                value="sesarjavier28@gmail.com\n⚡ Pagando con USDT vía Binance Pay tenes un 10% de descuento ( Binance ID: 552346130 )", 
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
                # Enviamos el texto y el embed juntos
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
        if not hasattr(interaction.channel, 'name') or not interaction.channel.name.startswith("ticket-"):
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
        if message.author.bot:
            return
            
        # Ignoramos si el mensaje empieza con / para no interferir con comandos de barra
        if message.content.startswith('/'):
            return

        # Solo escuchar en canales que empiecen con 'ticket-'
        if not hasattr(message.channel, 'name') or not message.channel.name.startswith("ticket-"):
            return

        # 0. Verificamos si la IA fue apagada en este ticket (Modo Manual)
        try:
            async with self.bot.pool.acquire(timeout=5.0) as conn:
                estado_ticket = await conn.fetchval("SELECT estado FROM tickets WHERE channel_id = $1", message.channel.id)
                if estado_ticket == 'pausado':
                    return # No hacemos nada, el bot ignora este canal
        except Exception:
            pass

        # --- LÓGICA DE MENCIONES (Titocalderon o Bot) ---
        # Si mencionan al bot o a vos, responde de forma atenta
        mencion_admin = f"<@{self.mi_id}>" in message.content or f"<@!{self.mi_id}>" in message.content
        if self.bot.user.mentioned_in(message) or mencion_admin:
            await message.reply("¡Hola! Gracias por etiquetarme.\nEstoy acá para ayudarte, ¿cuál es tu duda o problema?")
            return

        # --- ESCUDO ANTI-SPAM (Nivel Búnker) ---
        # Detecta patrones como discord.gg/abc, discord.com/invite/abc, discordapp.com/invite/abc
        patron_invitacion = r"(discord\.(gg|io|me|li|com\/invite)|discordapp\.com\/invite)\/([a-zA-Z0-9\-]+)"
        if re.search(patron_invitacion, message.content, re.IGNORECASE):
            # 1. Borrar el mensaje para que nadie más vea el link
            await message.delete()
            # 2. Banear al usuario de una (excepto si sos vos)
            if message.author.id != 704501115110162542: # Tu ID real
                try:
                    await message.author.ban(reason="Spam de invitaciones detectado en ticket.")
                    await message.channel.send(f"🚨 **Sistema de Seguridad**: Usuario {message.author.name} baneado permanentemente por spam de invitaciones.")
                    print(f"🔨 [BAN] {message.author.name} ({message.author.id}) por link de invitación.")
                    return # Cortamos la ejecución acá, el nabo ya no existe
                except discord.Forbidden:
                    await message.channel.send("⚠️ No tengo permisos para banear a este nabo, pero borré el link.")
                    return
        # --------------------------------------

        # 1. Registrar actividad en la base de datos (marcando que el usuario ya habló)
        await self._update_ticket_activity(message)

        # 2. Detección de Comprobantes (Mensajes con imágenes o PDF)
        has_valid_attachment = any(att.content_type and (att.content_type.startswith('image/') or att.content_type == 'application/pdf') for att in message.attachments)
        if message.attachments and has_valid_attachment:
            await self.handle_receipt_image(message)
            return

        # 3. Diccionario de respuestas rápidas para ahorrar API
        contenido_limpio = message.content.strip().lower()
        if contenido_limpio in RESPUESTAS_PREDEFINIDAS:
            await message.reply(RESPUESTAS_PREDEFINIDAS[contenido_limpio])
            return

        # 4. Soporte con IA Proactiva (Escucha siempre en el ticket para cerrar ventas)
        await self.handle_support_query(message)

    async def _update_ticket_activity(self, message: discord.Message):
        # Corrección: Extraer IDs directamente como enteros para compatibilidad con BIGINT
        channel_id = message.channel.id
        user_id = message.author.id
        
        try:
            async with self.bot.pool.acquire(timeout=5.0) as conn:
                # Evitamos el error de Foreign Key asegurando que el usuario exista
                await conn.execute("INSERT INTO usuarios (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING", user_id)
                
                query = """
                    INSERT INTO tickets (channel_id, user_id, estado, ultimo_mensaje, hablo)
                    VALUES ($1, $2, 'abierto', CURRENT_TIMESTAMP, TRUE)
                    ON CONFLICT (channel_id) DO UPDATE 
                    SET ultimo_mensaje = CURRENT_TIMESTAMP, hablo = TRUE
                """
                await conn.execute(query, channel_id, user_id)
        except Exception as e:
            print(f"❌ [DB Error] No se pudo actualizar actividad de ticket {channel_id}: {e}")

    async def handle_receipt_image(self, message: discord.Message):
        # Extraer el adjunto de imagen o PDF
        attachment = next((a for a in message.attachments if a.content_type.startswith('image/') or a.content_type == 'application/pdf'), None)
        if not attachment:
            return
            
        advertencia = await message.channel.send("⏳ **Auditoría IA**: Analizando comprobante de pago...")

        try:
            # Descargar imagen/pdf nativamente usando discord.py
            image_data = await attachment.read()
            image_parts = [{"mime_type": attachment.content_type, "data": image_data}]

            # Recopilar contexto reciente
            historial = []
            async for msg in message.channel.history(limit=5, before=message):
                if not msg.author.bot:
                    historial.append(f"{msg.author.name}: {msg.content}")
            
            contexto = "\n".join(historial)
            
            prompt = f"""
Contexto reciente del chat (puede contener el rol deseado o aclarar montos parciales):
{contexto}

SOS UN AUDITOR FINANCIERO ESTRICTO. Analizá esta imagen o PDF para validar si es un comprobante de transferencia COMPLETADO (ej: Mercado Pago, Banco, PayPal).
REGLA CRÍTICA Y ESTRICTA: Debes verificar OBLIGATORIAMENTE que el destinatario de la transferencia sea 'Fabrizio Giovanni Cocca Ducay' (o Fabrizio Cocca). Si logras leer el nombre del destinatario y es otra persona (por ejemplo, le están transfiriendo a un amigo u otro nombre), marca "es_comprobante": false.
REGLA 1: Buscá evidencia de que el pago finalizó (ej: "Transferencia exitosa", "Pago realizado"). Ignorá capturas de 'pre-transferencia' o pantallas de confirmación sin ejecutar.
REGLA 2: Si el formato numérico usa coma para miles (ej 4,100.00), convertilo a un número limpio (4100).
REGLA 3 DE MONTO RANDOM: 
- Si el monto NO coincide exactamente con un rango o combo, pero el usuario ESPECIFICÓ uno en el contexto, validalo contra ese.
- Si el monto es random y el usuario NO especificó qué quería, devolvé 'necesita_preguntar': true.
REGLA 4: Compará el monto pagado con nuestros precios estrictos:
- Diamante: $4100 ARS / $4 USD
- Oro: $3700 ARS / $3.5 USD
- Plata: $2100 ARS / $2 USD
- Diamante + Oro: $7800 ARS
- Diamante + Plata: $6200 ARS
- Oro + Plata: $5800 ARS
- LOS 3 RANGOS JUNTOS: $9900 ARS
REGLA 5: TUS DATOS DE COBRO (NUNCA INVENTES OTROS):
- Alias: LENGUA.LUJOSA.TELAR
- CVU: 0000168300000013531308
- PayPal (USD): sesarjavier28@gmail.com
- Binance Pay ID (USDT - 10% OFF): 552346130
- Titular: Fabrizio Giovanni Cocca Ducay (o Fabrizio Cocca)
REGLA 6: RESPUESTAS CORTAS: Respondé SIEMPRE a lo que se te pregunta y pide, podes dar algun que otro detalle util y adicional que consideres pero tampoco tanto. Sé directo, servicial y al grano. No uses lenguaje robótico ni des discursos largos.

Devolve ÚNICAMENTE un objeto JSON válido con la siguiente estructura (NO uses markdown ni comillas invertidas):
{{
  "es_comprobante": true_o_false,
  "monto": float,
  "moneda": "ARS_o_USD",
  "rol_detectado": "Diamante_Oro_Plata_o_Todos",
  "valido": true_o_false,
  "diferencia": float,
  "necesita_preguntar": true_o_false
}}
"""
            # Ejecutar modelo forzando exactamente gemini-flash-latest
            model = genai.GenerativeModel('gemini-flash-latest')
            
            # Agregamos timeout a nivel de asyncio para evitar bloqueos
            response = await asyncio.wait_for(
                asyncio.to_thread(model.generate_content, contents=[prompt, image_parts[0]]),
                timeout=30.0
            )
            
            # Limpieza y formateo del JSON
            text = response.text.strip()
            if text.startswith("```json"):
                text = text[7:-3].strip()
            elif text.startswith("```"):
                text = text[3:-3].strip()
                
            datos = json.loads(text)

            # Validaciones de negocio
            if not datos.get("es_comprobante"):
                await advertencia.edit(content="❌ **Auditoría Fallida**: No es válido, puede ser un fallo del bot, enviá la foto porfa y ahí vemos qué pasó.")
                return

            if datos.get("necesita_preguntar"):
                await advertencia.edit(content=f"🤔 **Monto no reconocido**: Detectamos un pago de **{datos.get('monto')} {datos.get('moneda')}**, pero no coincide con ningún rango exacto y no especificaste cuál querías en el chat. ¿Podés aclarar qué rangos estás comprando?")
                return

            rol = datos.get("rol_detectado")
            valido = datos.get("valido", False)
            diferencia = float(datos.get("diferencia", 0.0))

            if not rol or (rol not in ROLES and rol != "Todos" and "," not in rol):
                await advertencia.edit(content=f"⚠️ **Atención**: Comprobante de {datos.get('monto', 0)} {datos.get('moneda', '')} verificado, pero no alcanza o no concuerda para un rol específico. <@704501115110162542> revisalo manualmente.")
                return

            if not valido:
                faltante = abs(diferencia)
                await advertencia.edit(content=f"⚠️ **Comprobante Insuficiente**\nEl pago detectado de **{datos['monto']} {datos['moneda']}** no es suficiente para el rol solicitado.\nFaltan **{faltante} {datos['moneda']}**. Por favor, abona el resto y envía el nuevo comprobante.")
                return

            # Asignación de Roles múltiples o simples si es válido
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
                
                # Mensaje de éxito limpio con etiqueta correcta
                msg_exito = f"✅ **¡Pago Verificado con Éxito!**\nSe te ha otorgado el rol **{rol}** automáticamente.\n\n🔔 <@704501115110162542> auditoría automática completada."
                
                await advertencia.edit(content=msg_exito)
                
                # Marcar en NeonDB como completado
                await self._marcar_ticket_completado(message.channel.id)

                # Registrar el pago exitoso en la nueva tabla 'pagos'
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
            await advertencia.edit(content="⚠️ **Timeout**: La IA de auditoría tardó demasiado en responder. Intenta de nuevo más tarde.")
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

        prompt = f"""
Actúa como un asistente de ventas de Discord. Tu objetivo es cerrar la entrega de rangos.
HISTORIAL DE CONVERSACIÓN:
{contexto_previo}

REGLAS DE NEGOCIO Y RESPUESTA:
1. ARGUMENTO GOOGLE: Si el usuario dice que el contenido está gratis en Google o internet, responde que muchas chicas poco conocidas no aparecen ahí. Explica que el beneficio principal NO es solo el contenido, sino la COMODIDAD y el AHORRO DE TIEMPO: +200 canales ordenados en un solo lugar, a 2 clicks, sin anuncios, sin virus y todo centralizado de forma segura en Discord.
2. PRECIOS: Diamante ($4100 ARS), Oro ($3700 ARS), Plata ($2100 ARS). Rangos independientes. Dilos SOLO UNA VEZ al inicio o si el usuario pregunta explícitamente. NO los repitas constantemente como un loro.
3. SEGURIDAD CRÍTICA (ZERO TRUST): TIENES TOTALMENTE PROHIBIDO usar el comando [GRANT_ROLE] basándote únicamente en la palabra del usuario.
   - Si el usuario dice "ya pagué", "ya transferí", etc., exígele que envíe la imagen o archivo del comprobante por este medio.
   - SOLO puedes usar el comando si ves en el HISTORIAL que el sistema (el bot) ya validó físicamente una imagen enviando el mensaje de éxito y confirmando la asignación o el saldo disponible.
4. FUNCIONAMIENTO DE RANGOS (ESTRICTO): Los rangos NO son acumulativos ni desbloquean todo el servidor. El rango Diamante SOLO desbloquea el contenido Diamante. El rango Oro SOLO desbloquea Oro. El rango Plata SOLO desbloquea Plata. Si un usuario pregunta si un rango desbloquea "todos" los canales, tenés totalmente PROHIBIDO decirle que sí. Aclará explícitamente que cada rango da acceso ÚNICAMENTE a su propia categoría, a menos que el usuario pague por una combinación de rangos (ej: Diamante + Oro).

INSTRUCCIÓN TÉCNICA:
Si (y solo si) un pago previo fue validado por el sistema en el historial y el usuario aclara el rango que cubre ese pago, incluye al FINAL de tu respuesta este comando exacto: [GRANT_ROLE: NombreDelRol] (reemplaza NombreDelRol por Diamante, Oro o Plata).
Si no hay validación previa de imagen en el historial o falta dinero, pide el comprobante o explica la situación sin incluir el comando.

Consulta actual del usuario: "{message.content}"
"""
        try:
            async with message.channel.typing():
                model = genai.GenerativeModel('gemini-flash-latest')
                response = await asyncio.wait_for(
                    asyncio.to_thread(model.generate_content, contents=[prompt]),
                    timeout=30.0
                )
                respuesta_texto = response.text.strip()
                await message.reply(respuesta_texto)

                # Lógica para otorgar rol desde la conversación si la IA da la orden
                if "[GRANT_ROLE:" in respuesta_texto:
                    rol_nombre = respuesta_texto.split("[GRANT_ROLE:")[1].split("]")[0].strip()
                    if rol_nombre in ROLES:
                        role_obj = message.guild.get_role(ROLES[rol_nombre]["id"])
                        if role_obj:
                            await message.author.add_roles(role_obj, reason="Aclaración de rango vía IA")
                            await message.channel.send(f"✅ Sistema: Rol **{rol_nombre}** asignado tras aclaración.\n🔔 <@704501115110162542> auditoría manual/aclaración completada.")
                            await self._marcar_ticket_completado(message.channel.id)

                            # Registrar el pago exitoso en la tabla 'pagos' por aclaración manual de IA
                            try:
                                async with self.bot.pool.acquire(timeout=5.0) as conn:
                                    monto_estimado = ROLES[rol_nombre]["ars"] 
                                    await conn.execute(
                                        "INSERT INTO pagos (user_id, monto, moneda, rol) VALUES ($1, $2, $3, $4)",
                                        message.author.id, monto_estimado, "ARS", rol_nombre
                                    )
                            except Exception as e:
                                print(f"❌ [DB Error] No se pudo registrar el pago aclarado en la tabla 'pagos': {e}")

        except asyncio.TimeoutError:
            await message.reply("⚠️ La IA de soporte está congestionada, intenta preguntar de nuevo.")
        except Exception as e:
            print(f"❌ [IA Support Error]: {e}")

    # Tarea en loop cada 30 minutos para limpieza de tickets
    @tasks.loop(minutes=30)
    async def cleanup_tickets(self):
        # MODIFICADO: Completados a las 24hs
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
        # MODIFICADO: Inactivos a las 24hs (si habían hablado)
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

async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))