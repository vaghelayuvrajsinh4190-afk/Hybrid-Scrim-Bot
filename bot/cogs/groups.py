"""
Groups cog — /set_groups for dynamic group/lobby provisioning.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from shared.database import groups_col
from bot.utils.checks import admin_only

log = logging.getLogger(__name__)


class GroupsCog(commands.Cog, name="Groups"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="set_groups",
        description="Set the number of groups/lobbies — auto-creates or archives channels.",
    )
    @app_commands.describe(count="Target number of groups")
    @admin_only()
    async def set_groups(
        self, interaction: discord.Interaction, count: int,
    ) -> None:
        if count < 1 or count > 20:
            await interaction.response.send_message(
                "❌ Group count must be between 1 and 20.", ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        guild_id = guild.id

        # Find or create a "Groups" category
        groups_category = None
        for cat in guild.categories:
            if cat.name.lower() == "groups":
                groups_category = cat
                break
        if not groups_category:
            groups_category = await guild.create_category("Groups")

        # Get existing groups
        existing = await groups_col().find(
            {"guild_id": guild_id}
        ).sort("group_number", 1).to_list(length=100)
        existing_nums = {g["group_number"] for g in existing}

        created = []
        archived = []

        # Create missing groups
        for i in range(1, count + 1):
            if i not in existing_nums:
                text_ch = await guild.create_text_channel(
                    f"group-{i}", category=groups_category,
                )
                voice_ch = await guild.create_voice_channel(
                    f"Group {i} Voice", category=groups_category,
                )
                await groups_col().insert_one({
                    "guild_id": guild_id,
                    "group_number": i,
                    "channel_ids": {
                        "text": text_ch.id,
                        "voice": voice_ch.id,
                    },
                })
                created.append(i)

        # Archive extra groups (beyond target count)
        for g in existing:
            if g["group_number"] > count:
                ch_ids = g.get("channel_ids", {})
                for cid in ch_ids.values():
                    ch = guild.get_channel(cid)
                    if ch:
                        try:
                            await ch.delete(reason="Group archived")
                        except discord.Forbidden:
                            pass
                await groups_col().delete_one({"_id": g["_id"]})
                archived.append(g["group_number"])

        parts = []
        if created:
            parts.append(f"Created groups: {', '.join(str(c) for c in created)}")
        if archived:
            parts.append(f"Archived groups: {', '.join(str(a) for a in archived)}")
        if not parts:
            parts.append("No changes needed.")

        await interaction.followup.send(
            f"✅ Groups set to **{count}**.\n" + "\n".join(parts),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GroupsCog(bot))
