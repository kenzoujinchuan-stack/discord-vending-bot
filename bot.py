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

# 各種IDの設定
ADMIN_USER_ID = 1233691331214446605         # あなた（管理人）のユーザーID
REVIEW_CHANNEL_ID = 1546490480315859025    # 実績を流すチャンネルのID
ROLE_ID = 1546494120816541796              # 実績入力時に付与するロールのID

# 👇 【追加】お問い合わせを受け取る管理者用チャンネルのIDを指定してね！
INQUIRY_CHANNEL_ID = 1547123631430176828   # とりあえずログチャンネルと同じにしてるけど変えてOK！

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
# 🎫 お問い合わせ用システム（ここからドカンと追加！）
# ==========================================

# 1. ユーザーが入力するお問い合わせモーダル
class InquiryModal(discord.ui.Modal, title="お問い合わせフォーム"):
    inquiry_content = discord.ui.TextInput(
        label="お問い合わせ内容",
        placeholder="ここに質問や問題を詳しく書いてね！",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        content = self.inquiry_content.value

        # ① ユーザーのDMに控えを送信（DMブロックされてるかもなのでtry-except）
        dm_success = True
        try:
            dm_embed = discord.Embed(
                title="✅ お問い合わせ完了しました！",
                description="以下の内容で管理者にお問い合わせを送信したぜ！返信をお待ちくだせえ！",
                color=0x2ECC71
            )
            dm_embed.add_field(name="送信内容", value=content)
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            dm_success = False # DMブロックされてる奴おるな！

        # ② 管理者用チャンネルに送信（赤色🔴で「未返信」をアピール）
        inquiry_channel = interaction.guild.get_channel(INQUIRY_CHANNEL_ID)
        if inquiry_channel:
            admin_embed = discord.Embed(
                title="🔴 【未返信】新規お問い合わせ",
                description=f"**送信者:** {user.mention} (`{user.id}`)\n\n**【内容】**\n{content}",
                color=0xE74C3C # 赤色！
            )
            # ViewにユーザーIDを仕込んで、あとで誰に返信するか分かるようにする
            view = AdminReplyView(target_user_id=user.id, original_content=content)
            await inquiry_channel.send(embed=admin_embed, view=view)

        # ユーザーへの最終レスポンス
        msg = "✅ お問い合わせを送信しました！DMに控えを送ったよ！"
        if not dm_success:
            msg = "✅ お問い合わせを送信したよ！（※DMが設定でブロックされてるみたいだから控えは送れなかったぜ！BotからのDMをオンにしといてな！）"
        await interaction.followup.send(msg, ephemeral=True)


# 2. 最初にお問い合わせパネルに付いてるボタン（ユーザー用）
class InquiryMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 チケット発行（お問い合わせ）", style=discord.ButtonStyle.primary, custom_id="inquiry_open_btn")
    async def open_inquiry(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ボタン押したらモーダルをドーン！
        await interaction.response.send_modal(InquiryModal())


# 3. 管理者が返信するためのモーダル
class AdminReplyModal(discord.ui.Modal):
    reply_content = discord.ui.TextInput(
        label="返信内容",
        placeholder="ここにユーザーへの返信を入力してね！",
        style=discord.TextStyle.paragraph,
        required=True
    )

    def __init__(self, target_user_id: int, original_content: str, message_to_edit: discord.Message):
        super().__init__(title="お問い合わせへの返信")
        self.target_user_id = target_user_id
        self.original_content = original_content
        self.message_to_edit = message_to_edit # 後で色を変えるために元のメッセージを保持

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        guild = interaction.guild
        target_member = guild.get_member(self.target_user_id)
        reply_text = self.reply_content.value

        if not target_member:
            await interaction.followup.send("❌ ユーザーがサーバーから抜けちゃったか、見つからないぜ…", ephemeral=True)
            return

        # ① ユーザーのDMに返信を送信
        try:
            dm_embed = discord.Embed(
                title="📩 管理者からのお問い合わせ返信",
                color=0x3498DB
            )
            dm_embed.add_field(name="あなたのお問い合わせ", value=self.original_content, inline=False)
            dm_embed.add_field(name="管理者からの回答", value=reply_text, inline=False)
            await target_member.send(embed=dm_embed)
        except discord.Forbidden:
            await interaction.followup.send("❌ ユーザーがDMをブロックしてるから返信が送れなかったぜ！サーバーでメンションして呼ぶしかないかも！", ephemeral=True)
            return

        # ② 管理者チャンネルのメッセージを緑色🟢（対応完了）に更新
        updated_embed = discord.Embed(
            title="🟢 【返信済み】お問い合わせ",
            description=f"**送信者:** {target_member.mention} (`{self.target_user_id}`)\n\n**【内容】**\n{self.original_content}\n\n**【あなたの返信】**\n{reply_text}",
            color=0x2ECC71 # 緑色！
        )
        updated_embed.set_footer(text=f"対応者: {interaction.user.name}")
        
        # ボタンを無効化（もう返信済みにする）
        for item in self.message_to_edit.components[0].children:
            item.disabled = True
            
        await self.message_to_edit.edit(embed=updated_embed, view=discord.ui.View.from_message(self.message_to_edit))
        await interaction.followup.send("✅ ユーザーに返信を送信し、ステータスを更新したぜ！お疲れ！", ephemeral=True)


# 4. 管理者チャンネルの「返信」ボタン（ボス用）
class AdminReplyView(discord.ui.View):
    def __init__(self, target_user_id: int, original_content: str):
        super().__init__(timeout=None)
        self.target_user_id = target_user_id
        self.original_content = original_content

    @discord.ui.button(label="✍️ 返信する", style=discord.ButtonStyle.success, custom_id="admin_reply_btn")
    async def reply_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 権限チェック（一応ボスか管理者しか押せないようにしとく）
        if interaction.user.id != ADMIN_USER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ お前には触れないボタンだぜ！", ephemeral=True)
            return
        
        # モーダルを開く。引数に「元のメッセージ」を渡して、あとで色を変えられるようにする
        await interaction.response.send_modal(
            AdminReplyModal(
                target_user_id=self.target_user_id, 
                original_content=self.original_content, 
                message_to_edit=interaction.message
            )
        )

# ==========================================
# 🎫 チケット＆個室システムのビュー・モーダル群（既存のやつ）
# ==========================================

# 1. 注文時の情報入力用モーダル（商品名・PayPayリンク等）
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
        
        # 権限の設定：@everyoneは見れない、本人とBot、管理人が見れる
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        
        admin_member = guild.get_member(ADMIN_USER_ID)
        if admin_member:
            overwrites[admin_member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        # 安全な「ユーザーID」ベースのチャンネル名
        channel_name = f"ticket-{interaction.user.id}"
        
        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                topic=f"購入者: {interaction.user.id} ({interaction.user.name}) | 商品: {self.item_name} ({self.expected_amount}円)"
            )
        except Exception as e:
            print(f"チャンネル作成エラー: {e}")
            await interaction.followup.send(f"❌ チャンネルの作成に失敗しました……権限や設定を確認してね！\nエラー: `{e}`", ephemeral=True)
            return
        
        # DB記録
        details_list = [f"{label}: {input_item.value}" for label, input_item in self.inputs]
        full_details_str = "\n".join(details_list)
        save_history(interaction.user.id, str(interaction.user), self.item_name, self.expected_amount, full_details_str)

        await interaction.followup.send(f"✅ 専用の個室を作成しました！ 👉 {ticket_channel.mention}", ephemeral=True)
        
        # 【ステップ3】個室作成時の初期メッセージとボタン1個（ログイン情報入力ボタン）
        embed = discord.Embed(
            title="🛒 ご注文ありがとうございます！",
            description=(
                f"**購入者:** {interaction.user.mention}\n"
                f"**商品:** {self.item_name} ({self.expected_amount}円)\n\n"
                f"**提出された注文詳細:**\n{full_details_str}\n\n"
                "─────────────────────\n"
                "⚠️ **次のステップに進んでください** ⚠️\n"
                "下の**【🔑 LINEログイン情報を入力する】**ボタンを押して、\n"
                "ツムツム連携されているLINEのメールアドレスとパスワードを入力してください！"
            ),
            color=0x2ECC71
        )
        
        view = InitialLoginView(buyer_id=interaction.user.id)
        await ticket_channel.send(content=f"{interaction.user.mention} 様、専用取引ルームへようこそ！", embed=embed, view=view)


# 2. 初期画面のボタン（ログイン情報入力ボタン 1個のみ）
class InitialLoginView(discord.ui.View):
    def __init__(self, buyer_id: int):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id

    @discord.ui.button(label="🔑 LINEログイン情報を入力する", style=discord.ButtonStyle.primary, custom_id="ticket_input_login")
    async def open_login_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 購入者本人のみ操作可能
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("❌ これは購入者専用のボタンです！", ephemeral=True)
            return
            
        await interaction.response.send_modal(LoginModal(buyer_id=self.buyer_id))


# 3. LINEログイン情報入力用モーダル
class LoginModal(discord.ui.Modal, title="LINE ログイン情報入力"):
    line_email = discord.ui.TextInput(
        label="LINEメールアドレス",
        placeholder="example@line.com",
        style=discord.TextStyle.short,
        required=True
    )
    line_password = discord.ui.TextInput(
        label="LINEパスワード",
        placeholder="パスワードを入力してください",
        style=discord.TextStyle.short,
        required=True
    )

    def __init__(self, buyer_id: int):
        super().__init__()
        self.buyer_id = buyer_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # 【ステップ4】入力後のメッセージ（購入手続き完了 ＆ ログイン情報掲示 ＆ ボタン2個）
        embed = discord.Embed(
            title="✅ 購入手続きが完了しました！",
            description=(
                "ログイン情報の提出ありがとうございました！\n"
                "管理人が確認次第、作業を開始いたしますので今しばらくお待ちください。\n\n"
                "**【提出されたログイン情報】**\n"
                f"📧 **メールアドレス:** `{self.line_email.value}`\n"
                f"🔑 **パスワード:** `{self.line_password.value}`"
            ),
            color=0x3498DB
        )
        
        # 次のフェーズのボタン2個（「管理人を呼ぶ」＆「作業完了」）
        view = TicketRoomView(buyer_id=self.buyer_id)
        await interaction.channel.send(content=f"{interaction.user.mention} お手続き完了です！", embed=embed, view=view)


# 4. ログイン情報入力後のボタン2個（管理人呼ぶ ＆ 作業完了）
class TicketRoomView(discord.ui.View):
    def __init__(self, buyer_id: int):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id
        self.mentioned_count = 0

    @discord.ui.button(label="🔔 管理人を呼ぶ", style=discord.ButtonStyle.primary, custom_id="ticket_mention_admin")
    async def mention_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.mentioned_count >= 1:
            await interaction.response.send_message("❌ このボタンは1回しか使えません！管理人が気づくまでお待ちください。", ephemeral=True)
            return
        
        self.mentioned_count += 1
        await interaction.channel.send(f"🚨 買主から管理人のメンション要請がありました！ <@{ADMIN_USER_ID}>")
        await interaction.response.send_message("✅ 管理人に通知を送りました！", ephemeral=True)

    @discord.ui.button(label="✅ 作業完了（管理人専用）", style=discord.ButtonStyle.success, custom_id="ticket_complete_trade")
    async def complete_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != ADMIN_USER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ あなたにはこのボタンを押す権限がありません！", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🎉 代行作業が完了しました！",
            description=(
                "お疲れ様でした！これにて全ての代行作業は終了となります。\n"
                "よろしければ、下の**【実績を入力する】**ボタンから感想を教えてください！\n"
                "（感想を入力すると、自動でロールが付与されチャンネルを閉じられるようになります！）"
            ),
            color=0xF1C40F
        )
        
        view = AfterTradeView(buyer_id=self.buyer_id)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ 作業完了メッセージを送信しました。", ephemeral=True)


# 5. PIN入力要請時のボタンビュー
class PinEntryView(discord.ui.View):
    def __init__(self, buyer_id: int):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id

    @discord.ui.button(label="📌 PINを入力しました", style=discord.ButtonStyle.success, custom_id="ticket_pin_submitted")
    async def pin_submitted(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("❌ 購入者本人しかこのボタンは押せません！", ephemeral=True)
            return

        # 確認ダイアログ（はい／いいえ）を自分だけに表示（ephemeral）
        embed = discord.Embed(
            title="❓ 確認",
            description=(
                "**本当に入力しましたか？**\n"
                "⚠️ **※もし入力していないと購入（代行処理）が失敗します！**"
            ),
            color=0xE74C3C
        )
        view = PinConfirmYesNoView(buyer_id=self.buyer_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# 6. PIN入力確認（はい／いいえ）のビュー
class PinConfirmYesNoView(discord.ui.View):
    def __init__(self, buyer_id: int):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id

    @discord.ui.button(label="はい（入力済み）", style=discord.ButtonStyle.danger)
    async def confirm_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 自分用確認メッセージを消去/更新
        await interaction.response.edit_message(content="✅ 確認が完了しました！", embed=None, view=None)
        
        # チャンネル全体に通知メッセージを送信
        embed = discord.Embed(
            title="✅ PIN入力完了",
            description="PINコードの入力完了を確認しました。少々お待ちください！",
            color=0x2ECC71
        )
        await interaction.channel.send(content=f"<@{self.buyer_id}>", embed=embed)

    @discord.ui.button(label="いいえ（戻る）", style=discord.ButtonStyle.secondary)
    async def confirm_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="↩️ キャンセルしました。LINE側でPINコードを入力してから再度ボタンを押してください！", embed=None, view=None)


# 7. 取引完了後のボタン（実績入力 ＆ 強制破壊）
class AfterTradeView(discord.ui.View):
    def __init__(self, buyer_id: int):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id

    @discord.ui.button(label="⭐ 実績を入力する（感想を書く）", style=discord.ButtonStyle.danger, emoji="📝", custom_id="ticket_open_review")
    async def open_review_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ReviewModal(buyer_id=self.buyer_id))

    @discord.ui.button(label="🔥 強制破壊（管理人専用）", style=discord.ButtonStyle.secondary, emoji="🗑️", custom_id="ticket_force_delete")
    async def force_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != ADMIN_USER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ あなたにはこのボタンを押す権限がありません！", ephemeral=True)
            return
        
        try:
            buyer = interaction.guild.get_member(self.buyer_id)
            if buyer:
                await buyer.send("📢 あなたの専用取引チャンネルは管理人の手によって閉じられました。ご利用ありがとうございました！")
        except:
            pass
        
        await interaction.channel.delete()


# 8. 感想入力用のモーダル
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
        
        review_channel = guild.get_channel(REVIEW_CHANNEL_ID)
        if review_channel:
            embed = discord.Embed(
                title="🌟 新しいお客様の実績・ご感想！",
                description=self.review_text.value,
                color=0xE91E63
            )
            embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
            await review_channel.send(embed=embed)
            
        role = guild.get_role(ROLE_ID)
        if role:
            try:
                target_member = guild.get_member(self.buyer_id)
                if target_member:
                    await target_member.add_roles(role)
            except:
                pass
                
        embed = discord.Embed(
            title="✨ 実績のご協力ありがとうございます！",
            description="ロールの付与が完了しました！下のボタンを押すと、このチャンネルをいつでも安全に削除できます。",
            color=0x3498DB
        )
        view = FinalCloseView()
        await interaction.channel.send(embed=embed, view=view)
        await interaction.followup.send("✅ 実績を送信し、ロールが付与されました！ご協力ありがとうございます！", ephemeral=True)


# 9. 誰でも押せる最終削除ボタン
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
    
    # 既存のViewを登録
    bot.add_view(ShopMainView())
    bot.add_view(FinalCloseView())
    # 👇【追加】お問い合わせ用のViewもBot再起動時に動くように登録しとくぜ！
    bot.add_view(InquiryMainView())
    
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"[{len(synced)}] 個のコマンドを同期完了！")
    except Exception as e:
        print(f"同期エラー: {e}")

# ==========================================
# コマンド群
# ==========================================

# --- 既存コマンド：ショップパネル設置 ---
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
    
    embed.set_footer(text="©Shiroko!shop.")

    view = ShopMainView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ ショップパネルを設置しました！", ephemeral=True)


# 👇【追加コマンド】お問い合わせパネル設置
@bot.tree.command(name="inquiry_enter", description="【管理者専用】お問い合わせ用のチケットパネルを設置します")
@app_commands.checks.has_permissions(administrator=True)
async def create_inquiry(interaction: discord.Interaction):
    embed = discord.Embed(
        title="カテゴリー：お問い合わせ",
        description="下のボタンを押すことで、管理者へ直接お問い合わせができます！\nご不明な点や問題があればお気軽にどうぞ！",
        color=0x9B59B6
    )
    view = InquiryMainView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ お問い合わせパネルをバッチリ設置したぜ！", ephemeral=True)


# --- 管理者専用新規コマンド1：/processing (処理中テキスト) ---
@bot.tree.command(name="processing", description="【管理者専用】作業開始（処理中）の案内を投稿します")
@app_commands.checks.has_permissions(administrator=True)
async def processing_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="**対応を開始しました。**",
        description="数分お待ちください",
        color=0x9B59B6  # 紫色！
    )
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ 処理中テキストを出力しました！", ephemeral=True)


# --- 管理者専用新規コマンド2：/pin_entry (PINコード入力要請) ---
@bot.tree.command(name="pin_entry", description="【管理者専用】お客さんに4桁のPINコード入力を要請します")
@app_commands.describe(pin="4桁のPINコード")
@app_commands.checks.has_permissions(administrator=True)
async def pin_entry_cmd(interaction: discord.Interaction, pin: str):
    # 個室のトピック情報から購入者IDを特定（トピックから判定できない場合は実行チャンネル）
    buyer_id = None
    if interaction.channel.topic and "購入者: " in interaction.channel.topic:
        try:
            buyer_id = int(interaction.channel.topic.split("購入者: ")[1].split(" ")[0])
        except:
            pass
    
    if not buyer_id:
        buyer_id = interaction.user.id

    embed = discord.Embed(
        title="🔐 PINコードの入力をお願いします",
        description=(
            f"このpinを入力してください。\n\n"
            f"👉 **PINコード: `{pin}`**\n\n"
            "入力が完了したら、下の**【📌 PINを入力しました】**ボタンを押してください！"
        ),
        color=0xE67E22
    )
    
    view = PinEntryView(buyer_id=buyer_id)
    await interaction.channel.send(content=f"<@{buyer_id}> PINコード入力の要請です！", embed=embed, view=view)
    await interaction.response.send_message("✅ PINコード入力要請を投稿しました！", ephemeral=True)


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
