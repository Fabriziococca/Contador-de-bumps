import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncpg # Librería para Base de Datos

# Cargar variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
DISBOARD_ID = 302050872383242240

intents = discord.Intents.default()
intents.message_content = True 

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.pool = None # Aquí guardaremos la conexión a la base de datos

    async def setup_hook(self):
        # Nos conectamos a la base de datos al iniciar
        try:
            self.pool = await asyncpg.create_pool(dsn=DATABASE_URL)
            
            # Creamos la tabla si no existe (Magia SQL)
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS bumps (
                        user_id TEXT PRIMARY KEY,
                        count INTEGER DEFAULT 0
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
    if message.author.id == DISBOARD_ID:
        if message.interaction_metadata:
            usuario = message.interaction_metadata.user
            
            es_bump_valido = False
            for embed in message.embeds:
                if (embed.description and "Bumped" in embed.description) or embed.image:
                    es_bump_valido = True
                    break

            if es_bump_valido:
                user_id = str(usuario.id)
                
                # QUERY SQL PROFESIONAL: "Upsert"
                # Intenta insertar. Si ya existe el ID, actualiza sumando 1.
                query = """
                    INSERT INTO bumps (user_id, count) VALUES ($1, 1)
                    ON CONFLICT (user_id) DO UPDATE SET count = bumps.count + 1
                    RETURNING count
                """
                
                # Ejecutamos la query
                async with bot.pool.acquire() as conn:
                    nuevo_total = await conn.fetchval(query, user_id)
                
                await message.channel.send(f"📈 **Bump registrado** | {usuario.mention} tiene ahora {nuevo_total} bumps acumulados.")

    await bot.process_commands(message)

# --- COMANDO: RANKING (SQL) ---
@bot.tree.command(name="ranking", description="Top 10 usuarios con más bumps")
async def ranking(interaction: discord.Interaction):
    # Pedimos a la base de datos los 10 mejores
    query = "SELECT user_id, count FROM bumps ORDER BY count DESC LIMIT 10"
    
    async with bot.pool.acquire() as conn:
        filas = await conn.fetch(query)

    if not filas:
        await interaction.response.send_message("📭 Aún no hay registros en la base de datos.", ephemeral=True)
        return

    embed = discord.Embed(title="🏆 Ranking Global (En la Nube)", color=discord.Color.gold())
    texto_top = ""
    
    for i, fila in enumerate(filas):
        user_id = fila['user_id']
        cantidad = fila['count']
        
        medalla = "🥇" if i==0 else "🥈" if i==1 else "🥉" if i==2 else "🔹"
        texto_top += f"**{i+1}.** {medalla} <@{user_id}> : `{cantidad} bumps`\n"

    embed.add_field(name="Top 10", value=texto_top, inline=False)
    await interaction.response.send_message(embed=embed)

# --- COMANDO: MIS PUNTOS (SQL) ---
@bot.tree.command(name="mispuntos", description="Mira tus estadísticas")
async def mispuntos(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    query = "SELECT count FROM bumps WHERE user_id = $1"
    
    async with bot.pool.acquire() as conn:
        cantidad = await conn.fetchval(query, user_id)

    cantidad = cantidad or 0 # Si es None, es 0
    await interaction.response.send_message(f"Hola {interaction.user.mention}, llevas **{cantidad} bumps** registrados en la nube.", ephemeral=True)

bot.run(TOKEN)