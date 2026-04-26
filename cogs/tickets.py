import discord
from discord.ext import commands, tasks
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
    "adios bot": "¡Hasta luego! El ticket se cerrará automáticamente en 48hs si no hay más actividad.",
    "adios": "¡Hasta luego! El ticket se cerrará automáticamente en 48hs si no hay más actividad."
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
        query = """
            CREATE TABLE IF NOT EXISTS tickets (
                channel_id BIGINT PRIMARY KEY,
                user_id BIGINT,
                estado TEXT DEFAULT 'abierto',
                ultimo_mensaje TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """
        try:
            async with self.bot.pool.acquire(timeout=10.0) as conn:
                await conn.execute(query)
            print("✅ [Tickets] Tabla verificada/creada exitosamente.")
        except Exception as e:
            print(f"❌ [DB Error] Error al crear la tabla tickets: {e}")
            
        # Iniciar la limpieza periódica
        self.cleanup_tickets.start()

    async def cog_unload(self):
        self.cleanup_tickets.cancel()

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        """Lanza el mensaje de presentación y advertencia cuando se abre un ticket."""
        if isinstance(channel, discord.TextChannel) and channel.name.startswith("ticket-"):
            # Esperamos un momento para que el mensaje de Ticket Tool quede arriba
            await asyncio.sleep(1.5)
            
            bienvenida = (
                "¡Hola! Soy tu asistente de ventas automatizado. 🤖\n"
                "Estoy aquí para ayudarte a obtener tu rango de forma rápida.\n\n"
                "⚠️ **ADVERTENCIA DE SEGURIDAD**:\n"
                "El bot está entrenado para funcionar correctamente. Cualquier intento de bypassear "
                "o engañar al sistema para obtener rangos gratis resultará en un **BAN permanente** del servidor.\n\n"
                "Cada vez que entrego un rol, se notifica al administrador para que corrobore la transacción manualmente. "
                "Si se detecta un intento de fraude o comprobante falso, serás expulsado de inmediato. "
                "Gracias por los intentos de engaño, ya que cada error me ayuda a mejorar y entrenar mejor al sistema. 😎"
                "Ante cualquier falla del bot incluso en un correcto uso, si ya pagaron pueden etiquetar a @titocalderon para que revise manualmente y otorgue el rol si corresponde."
            )
            await channel.send(bienvenida)

    @commands.command(name="panic")
    async def panic_button(self, ctx):
        # ID estricto: Solo vos podés apretar este botón
        if ctx.author.id != 696515814526681088:
            return
            
        await ctx.send("🚨 **SISTEMA DE EMERGENCIA ACTIVADO.**\nCortando conexiones a la base de datos y apagando el bot inmediatamente...")
        print("🚨 [KILL-SWITCH] Activado por el dueño. Cerrando bot...")
        
        # Cierra la conexión y apaga el bot
        await self.bot.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # Solo escuchar en canales que empiecen con 'ticket-'
        if not hasattr(message.channel, 'name') or not message.channel.name.startswith("ticket-"):
            return

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

        # 1. Registrar actividad en la base de datos
        await self._update_ticket_activity(message)

        # 2. Detección de Comprobantes (Mensajes con imágenes)
        has_image = any(att.content_type and att.content_type.startswith('image/') for att in message.attachments)
        if message.attachments and has_image:
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
                    INSERT INTO tickets (channel_id, user_id, estado, ultimo_mensaje)
                    VALUES ($1, $2, 'abierto', CURRENT_TIMESTAMP)
                    ON CONFLICT (channel_id) DO UPDATE 
                    SET ultimo_mensaje = CURRENT_TIMESTAMP
                """
                await conn.execute(query, channel_id, user_id)
        except Exception as e:
            print(f"❌ [DB Error] No se pudo actualizar actividad de ticket {channel_id}: {e}")

    async def handle_receipt_image(self, message: discord.Message):
        # Extraer el adjunto de imagen
        attachment = next((a for a in message.attachments if a.content_type.startswith('image/')), None)
        if not attachment:
            return
            
        advertencia = await message.channel.send("⏳ **Auditoría IA**: Analizando comprobante de pago...")

        try:
            # Descargar imagen nativamente usando discord.py
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

SOS UN AUDITOR FINANCIERO ESTRICTO. Analizá esta imagen para validar si es un comprobante de transferencia COMPLETADO (ej: Mercado Pago, Banco, PayPal).
REGLA 1: Buscá evidencia de que el pago finalizó (ej: "Transferencia exitosa", "Pago realizado"). Ignorá capturas de 'pre-transferencia' o pantallas de confirmación sin ejecutar.
REGLA 2: A veces la gente pregunta a nombre de quien es la transferencia, si preguntan es a nombre de Fabrizio Giovanni Cocca Ducay (no lo digas porque si, decilo solo si el usuario lo menciona o pregunta explícitamente, por ej: si pregunta es a nombre de Fabrizio... ahi les decis que si y nada mas, no lo digas antes ni nada).
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
                await advertencia.edit(content="❌ **Auditoría Fallida**: La imagen no parece ser un comprobante de pago válido o es ilegible.")
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
                
                # Marcar en NeonDB como completado enviando el ID como entero
                await self._marcar_ticket_completado(message.channel.id)
                
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

        except asyncio.TimeoutError:
            await message.reply("⚠️ La IA de soporte está congestionada, intenta preguntar de nuevo.")
        except Exception as e:
            print(f"❌ [IA Support Error]: {e}")

    # Tarea en loop cada 1 hora para limpieza de tickets completados tras 48hs
    @tasks.loop(hours=1)
    async def cleanup_tickets(self):
        # INTERVAL '48 hours' es compatible con PostgreSQL/NeonDB
        query = """
            SELECT channel_id FROM tickets 
            WHERE estado = 'completado' 
            AND ultimo_mensaje <= CURRENT_TIMESTAMP - INTERVAL '48 hours'
        """
        try:
            async with self.bot.pool.acquire(timeout=15.0) as conn:
                records = await conn.fetch(query)
                
                for record in records:
                    channel_id = int(record['channel_id'])
                    channel = self.bot.get_channel(channel_id)
                    
                    if channel:
                        try:
                            await channel.delete(reason="Limpieza automática: 48hs tras ticket completado.")
                            print(f"🗑️ [Limpieza] Canal de ticket {channel_id} eliminado.")
                        except discord.Forbidden:
                            print(f"⚠️ [Limpieza] Sin permisos para borrar el canal {channel_id}.")
                            continue
                        except discord.HTTPException as e:
                            print(f"❌ [Limpieza] Error HTTP al borrar {channel_id}: {e}")
                            continue

                    # Eliminar de la BD pasando el INT directamente
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