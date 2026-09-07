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

# 各種IDの設定（ここを自分のサーバーの実際のIDに書き換えてね！）
ADMIN_USER_ID = 1233691331214446605         # あなた（管理人）のユーザーID
REVIEW_CHANNEL_ID = 1546490480315859025    # 実績を流すチャンネルのID
ROLE_ID = 1546494120816541796            # 実績入力時に付与するロールのID

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


# ==========================================
# 🎫 チケット＆個室システムのビュー・モーダル群
# ==========================================

# 1. 注文時の情報入力用モーダル（商品名と金額を動的に受け取る）
class OrderModal(discord.ui.Modal):
    def __init__(self, item_name: str, expected_amount: int, field_settings: list):
        super().__init__(title=f"{item_name} のご注文フォーム")
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
        await interaction.response.defer(ephemeral=True)
        
        guild = interaction.guild
        
        # 権限の設定：@everyoneは見れない、本人とBot、そして管理人が見れるようにする
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        
        # 管理人自身にも強制的に見れる権限を付与
        admin_member = guild.get_member(ADMIN_USER_ID)
        if admin_member:
            overwrites[admin_member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        # 専用チャンネルの名前を作る (例: ticket-username)
        channel_name = f"ticket-{interaction.user.name.lower()}"
        
        # 個室チャンネルを爆誕させる！
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            topic=f"購入者: {interaction.user.id} の専用取引ルーム | 商品: {self.item_name} ({self.expected_amount}円)"
        )
        
        # データベースに仮保存（または履歴として記録）
        details_list = [f"{label}: {input_item.value}" for label, input_item in self.inputs]
        full_details_str = "\n".join(details_list)
        save_history(interaction.user.id, str(interaction.user), self.item_name, self.expected_amount, full_details_str)

        # 本人にだけコッソリ個室の場所を教える
        await interaction.followup.send(f"✅ 専用の個室を作成しました！ 👉 {ticket_channel.mention}", ephemeral=True)
        
        # 個室の中に最初のメッセージとボタンを投下する！
        embed = discord.Embed(
            title="🛒 ご注文ありがとうございます！",
            description=(
                f"**購入者:** {interaction.user.mention}\n"
                f"**商品:** {self.item_name} ({self.expected_amount}円)\n\n"
                f"**提出された情報:**\n{full_details_str}\n\n"
                "ここはあなたと管理者だけの**完全プライベート個室**です。\n"
                "下のボタンから管理人を呼ぶか、取引が進むのをお待ちください！"
            ),
            color=0x2ECC71
        )
        
        # 第1段階のボタンビューをセット
        view = TicketRoomView(buyer_id=interaction.user.id)
        await ticket_channel.send(content=f"{interaction.user.mention} 担当者をお呼びします！", embed=embed, view=view)


# 2. 個室内の第1段階ボタン（管理人メンション ＆ 取引完了）
class TicketRoomView(discord.ui.View):
    def __init__(self, buyer_id: int):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id
        self.mentioned_count = 0  # メンション回数制限用

    @discord.ui.button(label="管理人をメンションする", style=discord.ButtonStyle.primary, emoji="🔔", custom_id="ticket_mention_admin")
    async def mention_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 1回しか使えない制限
        if self.mentioned_count >= 1:
            await interaction.response.send_message("❌ このボタンは1回しか使えません！管理人が気づくまでお待ちください。", ephemeral=True)
            return
        
        self.mentioned_count += 1
        await interaction.channel.send(f"🚨 買主から管理人のメンション要請がありました！ <@{ADMIN_USER_ID}>")
        await interaction.response.send_message("✅ 管理人に通知を送りました！", ephemeral=True)

    @discord.ui.button(label="取引完了（管理人専用）", style=discord.ButtonStyle.success, emoji="✅", custom_id="ticket_complete_trade")
    async def complete_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 管理人チェック（自分のユーザーIDか管理者権限を持っているか）
        if interaction.user.id != ADMIN_USER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ あなたにはこのボタンを押す権限がありません！", ephemeral=True)
            return
        
        # 取引完了後の新しいメッセージとボタンに切り替える！
        embed = discord.Embed(
            title="🎉 取引が完了しました！",
            description=(
                "お疲れ様でした！これにて代行作業は終了となります。\n"
                "よろしければ、下の**【実績を入力する】**ボタンから感想を教えてください！\n"
                "（感想を入力すると、自動でロールが貰えてチャンネルを閉じられるようになります！）"
            ),
            color=0xF1C40F
        )
        
        view = AfterTradeView(buyer_id=self.buyer_id)
        await interaction.message.edit(embed=embed, view=view)
        await interaction.response.send_message("✅ 取引完了ステータスに変更しました。", ephemeral=True)


# 3. 取引完了後のボタン（実績入力 ＆ 強制破壊）
class AfterTradeView(discord.ui.View):
    def __init__(self, buyer_id: int):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id

    @discord.ui.button(label="⭐ 実績を入力する（感想を書く）", style=discord.ButtonStyle.danger, emoji="📝", custom_id="ticket_open_review")
    async def open_review_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 実感入力用のモーダルを開く
        await interaction.response.send_modal(ReviewModal(buyer_id=self.buyer_id))

    @discord.ui.button(label="🔥 強制破壊（管理人専用）", style=discord.ButtonStyle.secondary, emoji="🗑️", custom_id="ticket_force_delete")
    async def force_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != ADMIN_USER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ あなたにはこのボタンを押す権限がありません！", ephemeral=True)
            return
        
        # お客さんにDMを送る試み
        try:
            buyer = interaction.guild.get_member(self.buyer_id)
            if buyer:
                await buyer.send("📢 あなたの専用取引チャンネルは管理人の手によって閉じられました。ご利用ありがとうございました！")
        except:
            pass
        
        await interaction.channel.delete()


# 4. 感想入力用のモーダル
class ReviewModal(discord.ui.Modal, title="お取引の感想・実績入力"):
    review_text = discord.ui.TextInput(
        label="ご感想・レビュー",
        placeholder="対応のスピードや感想を自由に書いてね！",
        style=discord.TextStyle.paragraph,
        required=True
    )

    def __init__(self, buyer_id: int):
        super().__init__()
        self.buyer_id = buyer_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        guild = interaction.guild
        
        # 実績チャンネルへ投稿する処理
        review_channel = guild.get_channel(REVIEW_CHANNEL_ID)
        if review_channel:
            embed = discord.Embed(
                title="🌟 新しいお客様の実績・ご感想！",
                description=self.review_text.value,
                color=0xE91E63
            )
            embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
            await review_channel.send(embed=embed)
            
        # 購入者に「実績入力ロール」を付与する処理
        role = guild.get_role(ROLE_ID)
        if role:
            try:
                target_member = guild.get_member(self.buyer_id)
                if target_member:
                    await target_member.add_roles(role)
            except:
                pass
                
        # チャンネルに「実績ありがとうございます！」と出し、誰でも押せる削除ボタンを出す
        embed = discord.Embed(
            title="✨ 実績のご協力ありがとうございます！",
            description="ロールの付与が完了しました！下のボタンを押すと、このチャンネルをいつでも安全に削除できます。",
            color=0x3498DB
        )
        view = FinalCloseView()
        await interaction.channel.send(embed=embed, view=view)
        await interaction.followup.send("✅ 実績を送信し、ロールが付与されました！ご協力ありがとうございます！", ephemeral=True)


# 5. 誰でも押せる最終削除ボタン
class FinalCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="チャンネルを閉じる（削除）", style=discord.ButtonStyle.danger, emoji="🚪", custom_id="ticket_final_close")
    async def close_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🚪 チャンネルを削除しています...", ephemeral=True)
        try:
            buyer = interaction.user
            await buyer.send("📢 取引チャンネルが閉じられました。ご利用ありがとうございました！")
        except:
            pass
        await interaction.channel.delete()


# ==========================================
# 🏪 ショップメインパネルのビュー群
# ==========================================
class ShopMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🪙 コイン購入 (¥700)", style=discord.ButtonStyle.primary, custom_id="shop_coin_btn_v3")
    async def buy_coin(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = [
            ("希望コイン数", "例: 1,000,000", False),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(OrderModal("コイン", 700, fields))

    @discord.ui.button(label="🎯 スコア購入 (¥700)", style=discord.ButtonStyle.primary, custom_id="shop_score_btn_v3")
    async def buy_score(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = [
            ("指定ツム・スコア", "例: バンビで1億点", False),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(OrderModal("スコア", 700, fields))

    @discord.ui.button(label="⭐ プレイヤーレベル (¥700)", style=discord.ButtonStyle.primary, custom_id="shop_plevel_btn_v3")
    async def buy_plevel(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = [
            ("目標レベル", "例: 1200まで", False),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(OrderModal("プレイヤーレベル", 700, fields))

    @discord.ui.button(label="🔥 ツムレベル (¥700)", style=discord.ButtonStyle.primary, custom_id="shop_tlevel_btn_v3")
    async def buy_tlevel(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = [
            ("対象ツム名", "例: ロマンスベル1つをレベル50", False),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(OrderModal("ツムレベル", 700, fields))

    @discord.ui.button(label="🎰 ガチャ (¥1,200)", style=discord.ButtonStyle.success, custom_id="shop_gacha_btn_v3")
    async def buy_gacha(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = [
            ("ガチャの種類と回数", "例: 好きなガチャをコイン分", False),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(OrderModal("ガチャ", 1200, fields))

    @discord.ui.button(label="💎 高品質コイン (¥1,700〜)", style=discord.ButtonStyle.danger, custom_id="shop_hqcoin_btn_v3")
    async def buy_hqcoin(self, interaction: discord.Interaction, button: discord.ui.Button):
        fields = [
            ("希望金額・詳細", "例: 指定ツムで回数分割コイン獲得", True),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(OrderModal("高品質コイン", 1700, fields))


# --- Bot起動処理 ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}!")
    
    # 永続Viewの登録（Bot再起動後もボタンが機能するようにする！）
    bot.add_view(ShopMainView())
    bot.add_view(FinalCloseView())
    
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"[{len(synced)}] 個のコマンドを同期完了！")
    except Exception as e:
        print(f"同期エラー: {e}")

# --- コマンド（ショップパネル設置） ---
@bot.tree.command(name="shop_enter", description="【管理者専用】ツムツム自動代行のショップパネルを設置します")
@app_commands.checks.has_permissions(administrator=True)
async def create_vending(interaction: discord.Interaction):
    embed = discord.Embed(
        title="ツムツム自動代行サービス",
        description="各メニューのボタンからご注文にお進みください。自動で専用の個室が作成されます！",
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

# --- 履歴確認コマンド ---
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
