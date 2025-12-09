# alterra_bot.py
# Requirements:
# pip install discord.py fastapi uvicorn python-dotenv

import os
import uuid
import asyncio
from typing import Dict
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
import uvicorn
from uvicorn.config import Config
from uvicorn.server import Server
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------
# CONFIG — ENV változók
# ---------------------------------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
PUBLIC_URL = os.environ.get("PUBLIC_URL")
VERIF_SECRET = os.environ.get("VERIF_SECRET")

GUILD_ID = int(os.environ.get("GUILD_ID", "0"))
VERIFIED_ROLE_ID = int(os.environ.get("VERIFIED_ROLE_ID", "0"))
SETUP_CHANNEL_ID = int(os.environ.get("SETUP_CHANNEL_ID", "0"))

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN nincs beállítva.")
if not PUBLIC_URL:
    raise RuntimeError("PUBLIC_URL nincs beállítva.")
if not VERIF_SECRET:
    raise RuntimeError("VERIF_SECRET nincs beállítva.")


# ---------------------------------------------------
# Állapot tároló
# ---------------------------------------------------
verification_state: Dict[int, Dict] = {}

# ---------------------------------------------------
# Discord Bot
# ---------------------------------------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
app = FastAPI()


# ---------------------------------------------------
# BUTTON – unified start_verification ID
# ---------------------------------------------------
class StartVerificationButton(discord.ui.View):
    def __init__(self, user_id: int, state: str):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.state = state

    @discord.ui.button(
        label="Start Verification",
        style=discord.ButtonStyle.primary,
        custom_id="start_verification"
    )
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Ez nem a te gombod.", ephemeral=True)

        url = f"{PUBLIC_URL}/start?state={self.state}"
        await interaction.response.send_message(f"Verification link:\n{url}", ephemeral=True)


class FinalConfirmButton(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(
        label="Confirm Verification",
        style=discord.ButtonStyle.success,
        emoji="☑️",
        custom_id="final_confirm"
    )
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Nem neked készült.", ephemeral=True)

        guild = interaction.client.get_guild(GUILD_ID)
        if not guild:
            return await interaction.response.send_message("Szerver nem elérhető.", ephemeral=True)

        member = guild.get_member(self.user_id)
        role = guild.get_role(VERIFIED_ROLE_ID)

        if not member or not role:
            return await interaction.response.send_message("Tag vagy szerep hiányzik.", ephemeral=True)

        await member.add_roles(role, reason="Verification complete")
        await interaction.response.send_message("Sikeres verifikáció.", ephemeral=True)


# ---------------------------------------------------
# !setup
# ---------------------------------------------------
@bot.command()
@commands.has_guild_permissions(administrator=True)
async def setup(ctx: commands.Context):
    if ctx.channel.id != SETUP_CHANNEL_ID:
        return

    try:
        await ctx.message.delete()
    except:
        pass

    embed = discord.Embed(
        title="Alterra Verification",
        description="Nyomd meg a gombot a verifikáció indításához.",
        color=0x00AEEF
    )

    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="Start Verification",
        style=discord.ButtonStyle.primary,
        custom_id="start_verification"
    ))

    await ctx.send(embed=embed, view=view)


# ---------------------------------------------------
# INTERACTION HANDLER
# ---------------------------------------------------
@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return

    cid = interaction.data.get("custom_id")

    if cid == "start_verification":
        uid = interaction.user.id
        state_token = str(uuid.uuid4())

        verification_state[uid] = {
            "state": state_token,
            "step1": False,
            "step2": False,
            "final_sent": False
        }

        url = f"{PUBLIC_URL}/start?state={state_token}"
        await interaction.response.send_message(f"Your verification link:\n{url}", ephemeral=True)


# ---------------------------------------------------
# VERIFICATION STEPS
# ---------------------------------------------------
async def send_final_confirmation(user_id: int):
    view = FinalConfirmButton(user_id)
    user = bot.get_user(user_id)

    if user:
        try:
            await user.send("Final step — katt a gombra:", view=view)
            return
        except discord.Forbidden:
            pass

    channel = bot.get_channel(SETUP_CHANNEL_ID)
    if channel:
        await channel.send(f"<@{user_id}> final step:", view=view)


async def mark_step1_pass(user_id: int):
    data = verification_state.get(user_id)
    if data:
        data["step1"] = True


async def mark_step2_pass(user_id: int):
    data = verification_state.get(user_id)
    if not data:
        return

    data["step2"] = True

    if data["step1"] and not data["final_sent"]:
        data["final_sent"] = True
        await send_final_confirmation(user_id)


# ---------------------------------------------------
# FASTAPI
# ---------------------------------------------------
@app.get("/start", response_class=HTMLResponse)
async def start_verification(state: str):
    for uid, data in verification_state.items():
        if data.get("state") == state:
            html = """
            <html><body>
            <h2>Alterra Verification</h2>
            <p>Valid token. Folytasd a backend ellenőrzésekkel.</p>
            </body></html>
            """
            return HTMLResponse(html, 200)

    raise HTTPException(404, "Invalid state token")


async def verify_secret(x_verif_secret: str = Header(None)):
    if x_verif_secret != VERIF_SECRET:
        raise HTTPException(401, "Invalid secret")


@app.post("/step1")
async def step1(state: str, x_verif_secret: str = Header(None)):
    await verify_secret(x_verif_secret)

    for uid, data in verification_state.items():
        if data.get("state") == state:
            await mark_step1_pass(uid)
            return {"result": "step1_pass", "user_id": uid}

    raise HTTPException(404, "Invalid state token")


@app.post("/step2")
async def step2(state: str, x_verif_secret: str = Header(None)):
    await verify_secret(x_verif_secret)

    for uid, data in verification_state.items():
        if data.get("state") == state:
            await mark_step2_pass(uid)
            return {"result": "step2_pass", "user_id": uid}

    raise HTTPException(404, "Invalid state token")


# ---------------------------------------------------
# RUN BOTH (API + BOT)
# ---------------------------------------------------
async def start_api():
    config = Config(app=app, host="0.0.0.0", port=8000, log_level="info")
    server = Server(config)
    await server.serve()


async def main():
    api_task = asyncio.create_task(start_api())
    bot_task = asyncio.create_task(bot.start(DISCORD_TOKEN))
    await asyncio.gather(api_task, bot_task)


if __name__ == "__main__":
    asyncio.run(main())
