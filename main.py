import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncpg 
from keep_alive import keep_alive 

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
DISBOARD_ID = 302050872383242240

# Intents necesarios para leer el contenido de los mensajes
intents = discord.Intents.default()
intents.message_content = True 

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.pool = None

    async def setup_hook(self):
        """Inicializa la DB y sincroniza comandos al arrancar."""
        try:
            self.pool = await asyncpg.create_pool(dsn=DATABASE_URL)
            
            # Schema: Clave compuesta (user_id, guild_id) para conteo por servidor
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS bumps (
                        user_id TEXT,
                        guild_id TEXT,
                        count INTEGER DEFAULT 0,
                        PRIMARY KEY (user_id, guild_id)
                    )
                """)
            print("✅ Base de datos conectada.")
            
            await self.tree.sync()
            print("🔄 Comandos Slash sincronizados.")
            
        except Exception as e:
            print(f"❌ Error DB: {e}")

bot = Bot()

@bot.event
async def on_message(message):
    if message.author.id != DISBOARD_ID:
        return

    # Validamos si es un bump legítimo revisando la interacción o embeds
    if message.interaction_metadata:
        usuario = message.interaction_metadata.user
        
        es_bump_valido = False
        for embed in message.embeds:
            if (embed.description and "Bumped" in embed.description) or embed.image:
                es_bump_valido = True
                break

        if es_bump_valido:
            user_id = str(usuario.id)
            guild_id = str(message.guild.id) 
            
            # Upsert: Inserta 1, o suma 1 si ya existe el registro
            query = """
                INSERT INTO bumps (user_id, guild_id, count) VALUES ($1, $2, 1)
                ON CONFLICT (user_id, guild_id) DO UPDATE SET count = bumps.count + 1
                RETURNING count
            """
            
            async with bot.pool.acquire() as conn:
                nuevo_total = await conn.fetchval(query, user_id, guild_id)
            
            await message.channel.send(f"📈 **Bump registrado** | {usuario.mention} tiene ahora {nuevo_total} bumps.")

    await bot.process_commands(message)

@bot.tree.command(name="ranking", description="Top 10 usuarios con más bumps en este servidor")
async def ranking(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    
    query = "SELECT user_id, count FROM bumps WHERE guild_id = $1 ORDER BY count DESC LIMIT 10"
    
    async with bot.pool.acquire() as conn:
        filas = await conn.fetch(query, guild_id)

    if not filas:
        await interaction.response.send_message("📭 Aún no hay registros.", ephemeral=True)
        return

    embed = discord.Embed(title=f"🏆 Ranking Local - {interaction.guild.name}", color=discord.Color.gold())
    texto_top = ""
    
    for i, fila in enumerate(filas):
        medalla = "🥇" if i==0 else "🥈" if i==1 else "🥉" if i==2 else "🔹"
        texto_top += f"**{i+1}.** {medalla} <@{fila['user_id']}> : `{fila['count']} bumps`\n"

    embed.add_field(name="Top 10", value=texto_top, inline=False)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="mispuntos", description="Mira tus estadísticas en este servidor")
async def mispuntos(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    guild_id = str(interaction.guild_id)

    query = "SELECT count FROM bumps WHERE user_id = $1 AND guild_id = $2"
    
    async with bot.pool.acquire() as conn:
        cantidad = await conn.fetchval(query, user_id, guild_id)

    cantidad = cantidad or 0 
    await interaction.response.send_message(f"Hola {interaction.user.mention}, llevas **{cantidad} bumps**.", ephemeral=True)

keep_alive()
bot.run(TOKEN)