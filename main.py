import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncpg 
from keep_alive import keep_alive 

# Cargar variables del archivo .env
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
DISBOARD_ID = 302050872383242240

# Configuración de permisos
intents = discord.Intents.default()
intents.message_content = True 

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.pool = None # Aca guardaremos la conexión a la base de datos

    async def setup_hook(self):
        # Nos conectamos a la base de datos al iniciar
        try:
            self.pool = await asyncpg.create_pool(dsn=DATABASE_URL)
            
            
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS bumps (
                        user_id TEXT,
                        guild_id TEXT,
                        count INTEGER DEFAULT 0,
                        PRIMARY KEY (user_id, guild_id)
                    )
                """)
            print("✅ Base de datos conectada y tabla verificada.")
            
            # Sincronizamos comandos
            await self.tree.sync()
            print("🔄 Comandos Slash sincronizados.")
            
        except Exception as e:
            print(f"❌ Error conectando a la base de datos: {e}")

bot = Bot()

@bot.event
async def on_message(message):
    # Verificamos que el mensaje venga de DISBOARD
    if message.author.id == DISBOARD_ID:
        if message.interaction_metadata:
            usuario = message.interaction_metadata.user
            
            # Verificamos si es un bump exitoso
            es_bump_valido = False
            for embed in message.embeds:
                if (embed.description and "Bumped" in embed.description) or embed.image:
                    es_bump_valido = True
                    break

            if es_bump_valido:
                user_id = str(usuario.id)
                guild_id = str(message.guild.id) 
                
                # QUERY SQL: Guardamos Usuario + Servidor
                query = """
                    INSERT INTO bumps (user_id, guild_id, count) VALUES ($1, $2, 1)
                    ON CONFLICT (user_id, guild_id) DO UPDATE SET count = bumps.count + 1
                    RETURNING count
                """
                
                async with bot.pool.acquire() as conn:
                    nuevo_total = await conn.fetchval(query, user_id, guild_id)
                
                await message.channel.send(f"📈 **Bump registrado** | {usuario.mention} tiene ahora {nuevo_total} bumps en este servidor.")

    await bot.process_commands(message)

# --- COMANDO: RANKING (LOCAL POR SERVIDOR) ---
@bot.tree.command(name="ranking", description="Top 10 usuarios con más bumps en este servidor")
async def ranking(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    
    # Filtramos SOLO los bumps de este servidor (WHERE guild_id = $1)
    query = "SELECT user_id, count FROM bumps WHERE guild_id = $1 ORDER BY count DESC LIMIT 10"
    
    async with bot.pool.acquire() as conn:
        filas = await conn.fetch(query, guild_id)

    if not filas:
        await interaction.response.send_message("📭 Aún no hay registros en este servidor.", ephemeral=True)
        return

    embed = discord.Embed(title=f"🏆 Ranking Local - {interaction.guild.name}", color=discord.Color.gold())
    texto_top = ""
    
    for i, fila in enumerate(filas):
        user_id = fila['user_id']
        cantidad = fila['count']
        
        medalla = "🥇" if i==0 else "🥈" if i==1 else "🥉" if i==2 else "🔹"
        texto_top += f"**{i+1}.** {medalla} <@{user_id}> : `{cantidad} bumps`\n"

    embed.add_field(name="Top 10", value=texto_top, inline=False)
    await interaction.response.send_message(embed=embed)

# --- COMANDO: MIS PUNTOS (LOCAL) ---
@bot.tree.command(name="mispuntos", description="Mira tus estadísticas en este servidor")
async def mispuntos(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild_id)

    # Buscamos tus puntos SOLO en este servidor
    query = "SELECT count FROM bumps WHERE user_id = $1 AND guild_id = $2"
    
    async with bot.pool.acquire() as conn:
        cantidad = await conn.fetchval(query, user_id, guild_id)

    cantidad = cantidad or 0 
    await interaction.response.send_message(f"Hola {interaction.user.mention}, llevas **{cantidad} bumps** en este servidor.", ephemeral=True)

# Prendemos el servidor web falso y luego el bot
keep_alive()
bot.run(TOKEN)