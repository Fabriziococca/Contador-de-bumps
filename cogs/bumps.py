import discord
from discord import app_commands
from discord.ext import commands
import asyncpg
import asyncio

DISBOARD_ID = 302050872383242240

class Bumps(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Evitamos procesar mensajes que no sean de DISBOARD
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
                
                try:
                    # Timeout para evitar bloqueos por problemas de red con NeonDB
                    async with self.bot.pool.acquire(timeout=10.0) as conn:
                        nuevo_total = await conn.fetchval(query, user_id, guild_id)
                    
                    await message.channel.send(f"📈 **Bump registrado** | {usuario.mention} tiene ahora {nuevo_total} bumps.")
                except asyncio.TimeoutError:
                    print(f"⚠️ [TimeOut] Excedido tiempo de espera al registrar bump de {usuario.id}.")
                    await message.channel.send("⚠️ La base de datos está tardando en responder. El bump no pudo ser registrado.")
                except asyncpg.PostgresError as e:
                    print(f"❌ [DB Error] Fallo al insertar bump para {usuario.id}: {e}")
                    await message.channel.send("❌ Hubo un error de base de datos al guardar el bump.")
                except Exception as e:
                    print(f"❌ [Error Inesperado] en on_message (Bumps): {e}")

    @app_commands.command(name="ranking", description="Top 10 usuarios con más bumps en este servidor")
    async def ranking(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        
        query = "SELECT user_id, count FROM bumps WHERE guild_id = $1 ORDER BY count DESC LIMIT 10"
        
        try:
            # Añadimos manejo de timeouts y posibles desconexiones temporales
            async with self.bot.pool.acquire(timeout=10.0) as conn:
                filas = await conn.fetch(query, guild_id)

            if not filas:
                await interaction.response.send_message("📭 Aún no hay registros en este servidor.", ephemeral=True)
                return

            embed = discord.Embed(title=f"🏆 Ranking Local - {interaction.guild.name}", color=discord.Color.gold())
            texto_top = ""
            
            for i, fila in enumerate(filas):
                medalla = "🥇" if i==0 else "🥈" if i==1 else "🥉" if i==2 else "🔹"
                texto_top += f"**{i+1}.** {medalla} <@{fila['user_id']}> : `{fila['count']} bumps`\n"

            embed.add_field(name="Top 10", value=texto_top, inline=False)
            await interaction.response.send_message(embed=embed)
            
        except asyncio.TimeoutError:
            print(f"⚠️ [TimeOut] Consultando ranking en guild {guild_id}.")
            await interaction.response.send_message("⚠️ La base de datos tardó mucho en responder. Intenta de nuevo en unos segundos.", ephemeral=True)
        except asyncpg.PostgresError as e:
            print(f"❌ [DB Error] Consultando ranking en guild {guild_id}: {e}")
            await interaction.response.send_message("❌ Error de base de datos al consultar el ranking.", ephemeral=True)
        except Exception as e:
            print(f"❌ [Error Inesperado] en comando ranking: {e}")
            await interaction.response.send_message("❌ Ocurrió un error inesperado al intentar obtener el ranking.", ephemeral=True)

    @app_commands.command(name="mispuntos", description="Mira tus estadísticas en este servidor")
    async def mispuntos(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id)

        query = "SELECT count FROM bumps WHERE user_id = $1 AND guild_id = $2"
        
        try:
            async with self.bot.pool.acquire(timeout=10.0) as conn:
                cantidad = await conn.fetchval(query, user_id, guild_id)

            cantidad = cantidad or 0 
            await interaction.response.send_message(f"Hola {interaction.user.mention}, llevas **{cantidad} bumps**.", ephemeral=True)
            
        except asyncio.TimeoutError:
            print(f"⚠️ [TimeOut] Consultando mispuntos para {user_id}.")
            await interaction.response.send_message("⚠️ La base de datos está saturada. Intenta más tarde.", ephemeral=True)
        except asyncpg.PostgresError as e:
            print(f"❌ [DB Error] Consultando mispuntos para {user_id}: {e}")
            await interaction.response.send_message("❌ Error interno al consultar tus puntos.", ephemeral=True)
        except Exception as e:
            print(f"❌ [Error Inesperado] en comando mispuntos: {e}")
            await interaction.response.send_message("❌ Ocurrió un error inesperado al consultar tus puntos.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Bumps(bot))
