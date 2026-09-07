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

    @discord.ui.button(label="承認（支払い完了）", style=discord.ButtonStyle.success, custom_id="admin_approve_v11")
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

    @discord.ui.button(label="拒否（エラー）", style=discord.ButtonStyle.danger, custom_id="admin_reject_v11")
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

# --- 最終確認ビュー（注文確定 or キャンセル） ---
class OrderConfirmView(discord.ui.View):
    def __init__(self, item_name: str, amount: int, details_text: str):
        super().__init__(timeout=180)
        self.item_name = item_name
        self.amount = amount
        self.details_text = details_text

    @discord.ui.button(label="注文を確定", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="🎉 注文が正常に送信されました！管理者からの確認をお待ちください。", view=None)

        admin_channel = interaction.client.get_channel(ADMIN_LOG_CHANNEL_ID)
        if admin_channel:
            embed = discord.Embed(title="🚨 新しい購入申請が届きました！", color=0xFFD700)
            embed.add_field(name="購入者", value=interaction.user.mention, inline=False)
            embed.add_field(name="商品名", value=self.item_name, inline=True)
            embed.add_field(name="請求金額", value=f"{self.amount} 円", inline=True)
            embed.add_field(name="📌 提出された詳細情報", value=self.details_text, inline=False)

            view = AdminActionView(
                customer_user=interaction.user,
                item_name=self.item_name,
                amount=self.amount,
                details_text=self.details_text
            )
            await admin_channel.send(embed=embed, view=view)

    @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary)
    async def cancel_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ 注文をキャンセルしました。", view=None)

# --- 入力モーダル（ユーザーが購入時に必要事項を入れる画面） ---
class DynamicCustomerPayModal(discord.ui.Modal):
    def __init__(self, item_name: str, expected_amount: int, field_settings: list):
        super().__init__(title=f"{item_name} のご注文")
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
        details_list = []
        for label, input_item in self.inputs:
            val = input_item.value or "（未入力）"
            details_list.append(f"{label}:\n{val}")

        full_details_str = "\n\n".join(details_list)

        embed_confirm = discord.Embed(
            title="注文内容の確認",
            description=f"商品: {self.item_name}\n金額: {self.expected_amount}円\n\n" + \
                        "\n".join([f"**{l}**:\n{i.value}" for l, i in self.inputs]) + \
                        "\n\n内容を確認して「注文を確定」してください。\n※確定は1回のみ有効です。処理中は連打しないでください。",
            color=0x2B2D31
        )

        embed_warning = discord.Embed(
            title="※注意事項",
            description="1コイン〜2億コインの間で、お好きな数値をご指定いただけます。\n\n"
                        "※1か月に2億コイン以上を獲得するとBAN対象となる可能性があります。必ず2億以内に設定する、または月間合計が2億以内に収まるようにご指定ください。\n"
                        "現在のステータスが完了になってからツムツムにログインをお願いいたします。\n\n"
                        "なお、BANされた場合の補償はいたしかねますので、あらかじめご了承ください。",
            color=0xFEE75C
        )

        view = OrderConfirmView(
            item_name=self.item_name,
            amount=self.expected_amount,
            details_text=full_details_str
        )

        await interaction.response.send_message(embeds=[embed_confirm, embed_warning], view=view, ephemeral=True)

# --- 各商品ごとの購入ボタンを持つView ---
class ShopMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🪙 コイン購入 (¥700)", style=discord.ButtonStyle.primary, custom_id="shop_coin_btn_v3")
    async def buy_coin(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = [
            ("希望コイン数", "例: 1,000,000", False),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(DynamicCustomerPayModal("コイン", 700, fields))

    @discord.ui.button(label="🎯 スコア購入 (¥700)", style=discord.ButtonStyle.primary, custom_id="shop_score_btn_v3")
    async def buy_score(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = [
            ("指定ツム・スコア", "例: バンビで1億点", False),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(DynamicCustomerPayModal("スコア", 700, fields))

    @discord.ui.button(label="⭐ プレイヤーレベル (¥700)", style=discord.ButtonStyle.primary, custom_id="shop_plevel_btn_v3")
    async def buy_plevel(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = [
            ("目標レベル", "例: 1200まで", False),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(DynamicCustomerPayModal("プレイヤーレベル", 700, fields))

    @discord.ui.button(label="🔥 ツムレベル (¥700)", style=discord.ButtonStyle.primary, custom_id="shop_tlevel_btn_v3")
    async def buy_tlevel(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = [
            ("対象ツム名", "例: ロマンスベル1つをレベル50", False),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(DynamicCustomerPayModal("ツムレベル", 700, fields))

    @discord.ui.button(label="🎰 ガチャ (¥1,200)", style=discord.ButtonStyle.success, custom_id="shop_gacha_btn_v3")
    async def buy_gacha(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = [
            ("ガチャの種類と回数", "例: 好きなガチャをコイン分", False),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(DynamicCustomerPayModal("ガチャ", 1200, fields))

    @discord.ui.button(label="💎 高品質コイン (¥1,700〜)", style=discord.ButtonStyle.danger, custom_id="shop_hqcoin_btn_v3")
    async def buy_hqcoin(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = [
            ("希望金額・詳細", "例: 指定ツムで回数分割コイン獲得", True),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(DynamicCustomerPayModal("高品質コイン", 1700, fields))

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

# --- コマンド（自動で綺麗にパネルを設置する本来の仕様） ---
@bot.tree.command(name="create_vending", description="【管理者専用】ツムツム自動代行のショップパネルを設置します")
@app_commands.checks.has_permissions(administrator=True)
async def create_vending(interaction: discord.Interaction):
    embed = discord.Embed(
        title="ツムツム自動代行サービス",
        description="各メニューには注意事項がありますので、ご注文前にお読みください。",
        color=0x2B2D31
    )
    embed.add_field(name="コイン ¥700", value="`0-2億コインまで指定可能`", inline=False)
    embed.add_field(name="スコア ¥700", value="`指定ツムで指定スコアにする`", inline=False)
    embed.add_field(name="プレイヤーレベル ¥700", value="`1200まで指定可能`", inline=False)
    embed.add_field(name="ツムレベル ¥700", value="`指定ツム1つをレベル50まで上げる`", inline=False)
    embed.add_field(name="ガチャ ¥1,200", value="`好きなガチャをコイン分引く +BOXは対象外`", inline=False)
    embed.add_field(name="高品質コイン ¥1,700〜¥7,200", value="`指定ツムで回数分割コイン獲得（履歴に最大枚数のみ表示）`", inline=False)
    
    embed.set_footer(text="© 2026 GodMart All Rights Reserved.")

    view = ShopMainView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ ショップパネルを設置しました！", ephemeral=True)

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
