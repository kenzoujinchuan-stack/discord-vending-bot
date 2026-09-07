import os
import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
from datetime import datetime
from flask import Flask
from threading import Thread

# === WEBサーバー（Renderの無料Web Service用） ===
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_web():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_web)
    t.daemon = True
    t.start()

# === 設定値 ===
TOKEN = os.getenv("TOKEN")
ADMIN_LOG_CHANNEL_ID = 1500206540517540031  # 管理者用ログチャンネルのID
GUILD_ID = 1500129771441492219              # 自分のDiscordサーバーID

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- DB初期化 ---
def init_db():
    conn = sqlite3.connect("vending_history.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            item_name TEXT,
            amount INTEGER,
            paypay_link TEXT,
            processed_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def save_history(user_id: int, user_name: str, item_name: str, amount: int, details: str):
    conn = sqlite3.connect("vending_history.db")
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO history (user_id, user_name, item_name, amount, paypay_link, processed_at)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, user_name, item_name, amount, details, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

# --- 管理者承認ボタン ---
class AdminActionView(discord.ui.View):
    def __init__(self, customer_user: discord.User, item_name: str, amount: int, details_text: str):
        super().__init__(timeout=None)
        self.customer_user = customer_user
        self.item_name = item_name
        self.amount = amount
        self.details_text = details_text

    @discord.ui.button(label="承認（支払い完了）", style=discord.ButtonStyle.success, custom_id="admin_approve_v8")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        save_history(self.customer_user.id, str(self.customer_user), self.item_name, self.amount, self.details_text)

        dm_success = True
        try:
            embed = discord.Embed(
                title="✅ お支払いを確認しました！",
                description=f"ご利用ありがとうございます！支払いが承認されました。\n\n**商品名**: {self.item_name}\n**金額**: {self.amount}円",
                color=0x00FF00
            )
            embed.set_footer(text="商品の受け渡しや案内まで今しばらくお待ちください。")
            await self.customer_user.send(embed=embed)
        except discord.Forbidden:
            dm_success = False

        for child in self.children:
            child.disabled = True
            
        status_text = "【処理完了・DM送信済】" if dm_success else "【処理完了・DM送信失敗（ユーザーのDM閉鎖）】"
        await interaction.response.edit_message(content=f"{status_text} {interaction.user.mention} が承認しました。", view=self)

    @discord.ui.button(label="拒否（エラー）", style=discord.ButtonStyle.danger, custom_id="admin_reject_v8")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            embed = discord.Embed(
                title="❌ 支払いを処理できませんでした",
                description=f"**商品名**: {self.item_name}\n送信された情報が無効か、金額が一致しませんでした。確認の上、再度お試しください。",
                color=0xFF0000
            )
            await self.customer_user.send(embed=embed)
        except discord.Forbidden:
            pass

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"【拒否済】{interaction.user.mention} が拒否しました。", view=self)

# --- お客さんが入力する動的入力フォーム ---
class DynamicCustomerPayModal(discord.ui.Modal):
    def __init__(self, item_name: str, expected_amount: int, field_settings: list):
        super().__init__(title=f"{item_name} の購入手続き")
        self.item_name = item_name
        self.expected_amount = expected_amount
        self.inputs = []

        for label, placeholder, is_large in field_settings:
            style_type = discord.TextStyle.paragraph if is_large else discord.TextStyle.short
            text_input = discord.ui.TextInput(
                label=label[:45],
                placeholder=placeholder if placeholder else "入力してください",
                style=style_type,
                required=True
            )
            self.inputs.append((label, text_input))
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("情報を送信しました！確認まで少々お待ちください。（結果はDMに届きます）", ephemeral=True)

        admin_channel = interaction.client.get_channel(ADMIN_LOG_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(title="🚨 新しい購入申請が届きました！", color=0xFFD700)
            embed.add_field(name="購入者", value=interaction.user.mention, inline=False)
            embed.add_field(name="商品名", value=self.item_name, inline=True)
            embed.add_field(name="請求金額", value=f"{self.expected_amount} 円", inline=True)

            details_list = []
            for label, input_item in self.inputs:
                val = input_item.value or "（未入力）"
                embed.add_field(name=f"📌 {label}", value=f"```\n{val}\n```", inline=False)
                details_list.append(f"{label}: {val}")

            full_details_str = " | ".join(details_list)

            view = AdminActionView(
                customer_user=interaction.user,
                item_name=self.item_name,
                amount=self.expected_amount,
                details_text=full_details_str
            )
            await admin_channel.send(embed=embed, view=view)

# --- セレクトメニュー（プルダウン） ---
class ServiceSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="コイン", description="¥500 - 0-2億コインまで指定可能", emoji="🪙"),
            discord.SelectOption(label="スコア", description="¥500 - 指定ツムで指定スコアにする", emoji="🎯"),
            discord.SelectOption(label="プレイヤーレベル", description="¥500 - 1200まで指定可能", emoji="⭐"),
            discord.SelectOption(label="ツムレベル", description="¥500 - 指定ツム1つをレベル50まで上げる", emoji="🔥"),
            discord.SelectOption(label="ガチャ", description="¥1,000 - 好きなガチャをコイン分引く", emoji="🎰"),
        ]
        super().__init__(placeholder="ご希望の代行メニューを選択してください", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        
        # 選択されたメニューに応じて商品名・金額・入力欄を設定
        if selected == "コイン":
            item_name = "コイン代行"
            amount = 500
            fields = [("指定コイン数・PayPayリンク", "例: 1億コイン / https://...", True)]
        elif selected == "スコア":
            item_name = "スコア代行"
            amount = 500
            fields = [("指定ツム・スコア・リンク", "例: バンビで1億点 / https://...", True)]
        elif selected == "プレイヤーレベル":
            item_name = "プレイヤーレベル代行"
            amount = 500
            fields = [("目標レベル・リンク", "例: レベル1200 / https://...", True)]
        elif selected == "ツムレベル":
            item_name = "ツムレベル代行"
            amount = 500
            fields = [("対象ツム名・リンク", "例: ロマンスベルLv50 / https://...", True)]
        else:
            item_name = "ガチャ代行"
            amount = 1000
            fields = [("ガチャの種類・回数・リンク", "例: セレクト30連 / https://...", True)]

        await interaction.response.send_modal(DynamicCustomerPayModal(item_name, amount, fields))

# --- セレクトメニューを保持するView ---
class ServiceSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ServiceSelect())

# --- Bot起動処理 ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}!")
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"[{len(synced)}] 個のコマンドを同期完了！")
    except Exception as e:
        print(f"同期エラー: {e}")

# --- コマンド ---
@bot.tree.command(name="create_vending", description="【管理者専用】セレクトメニュー付きの代行ショップパネルを設置します")
@app_commands.checks.has_permissions(administrator=True)
async def create_vending(interaction: discord.Interaction):
    embed = discord.Embed(
        title="✨ ツムツム自動代行サービス ✨",
        description="各メニューには注意事項がありますので、ご注文前にご確認ください。\n下のメニューからご希望の代行内容を選択してください。",
        color=0x00FFC8
    )
    embed.set_footer(text="© 2026 GodMart All Rights Reserved.")

    view = ServiceSelectView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ セレクトメニュー付きの代行パネルを設置しました！", ephemeral=True)

@bot.tree.command(name="history", description="【管理者専用】取引履歴を確認します（自分だけに表示）")
@app_commands.checks.has_permissions(administrator=True)
async def history(interaction: discord.Interaction, user: discord.User = None):
    conn = sqlite3.connect("vending_history.db")
    cursor = conn.cursor()

    if user:
        cursor.execute("SELECT user_name, item_name, amount, paypay_link, processed_at FROM history WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user.id,))
        title_str = f"📜 {user.name} さんの取引履歴 (直近10件)"
    else:
        cursor.execute("SELECT user_name, item_name, amount, paypay_link, processed_at FROM history ORDER BY id DESC LIMIT 10")
        title_str = "📜 全ユーザーの取引履歴 (直近10件)"

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("該当する取引履歴は見つかりませんでした。", ephemeral=True)
        return

    embed = discord.Embed(title=title_str, color=0x7289DA)
    for row in rows:
        u_name, item, amt, link, p_date = row
        embed.add_field(
            name=f"{p_date} - {u_name}",
            value=f"商品: **{item}** ({amt}円)\n詳細: {link}",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

# === Webサーバーをバックグラウンド起動してからBotを開始！ ===
keep_alive()
bot.run(TOKEN)
