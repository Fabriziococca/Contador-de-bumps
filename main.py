import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncpg
import asyncio
from keep_alive import keep_alive 

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Intents necesarios para leer el contenido de los mensajes
intents = discord.Intents.default()
intents.message_content = True 

class Bot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.pool = None

    async def setup_hook(self):
        """Inicializa la DB, carga los Cogs y sincroniza comandos al arrancar."""
        print("Iniciando setup_hook...")
        
        # 1. Configuración y conexión robusta a la base de datos NeonDB (asyncpg)
        try:
            print("Conectando al pool de base de datos...")
            self.pool = await asyncpg.create_pool(
                dsn=DATABASE_URL,
                min_size=1,
                max_size=10,
                command_timeout=60.0,
                timeout=15.0, # Timeout general de conexión
                max_inactive_connection_lifetime=300.0
            )
            
            # Schema: Clave compuesta (user_id, guild_id) para conteo por servidor
            async with self.pool.acquire(timeout=10.0) as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS bumps (
                        user_id TEXT,
                        guild_id TEXT,
                        count INTEGER DEFAULT 0,
                        PRIMARY KEY (user_id, guild_id)
                    )
                """)
            print("✅ Base de datos y pool inicializados correctamente.")
            
        except asyncio.TimeoutError:
            print("❌ [CRITICAL] Timeout: No se pudo conectar a la base de datos a tiempo.")
            await self.close()
            return
        except asyncpg.PostgresError as e:
            print(f"❌ [CRITICAL] Error de PostgreSQL al iniciar el pool: {e}")
            await self.close()
            return
        except Exception as e:
            print(f"❌ [CRITICAL] Error inesperado en la base de datos: {e}")
            await self.close()
            return

        # 2. Cargar todos los módulos/Cogs
        cogs_dir = './cogs'
        if not os.path.exists(cogs_dir):
            os.makedirs(cogs_dir)
            print(f"📁 Directorio {cogs_dir} creado.")

        for filename in os.listdir(cogs_dir):
            if filename.endswith('.py'):
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    print(f"📦 Cog cargado correctamente: {filename}")
                except Exception as e:
                    print(f"❌ [ERROR] Falló la carga del cog {filename}: {e}")

        # 3. Sincronizar el árbol de comandos para los slash commands
        try:
            print("🔄 Sincronizando comandos Slash...")
            synced = await self.tree.sync()
            print(f"✅ Sincronizados {len(synced)} comandos Slash a nivel global.")
        except discord.HTTPException as e:
            print(f"❌ [ERROR] Falló la sincronización con la API de Discord: {e}")
        except Exception as e:
            print(f"❌ [ERROR] Error inesperado al sincronizar comandos: {e}")

bot = Bot()

@bot.event
async def on_ready():
    print(f"🤖 Bot conectado como {bot.user} (ID: {bot.user.id})")
    print("=======================================")

if __name__ == '__main__':
    # Mantenemos vivo el servidor flask
    keep_alive()
    
    if not TOKEN:
        print("❌ [CRITICAL] DISCORD_TOKEN no encontrado en el archivo .env.")
    elif not DATABASE_URL:
        print("❌ [CRITICAL] DATABASE_URL no encontrado en el archivo .env.")
    else:
        try:
            print("🚀 Iniciando el bot...")
            bot.run(TOKEN)
        except Exception as e:
            print(f"❌ [CRITICAL] El bot falló durante la ejecución: {e}")