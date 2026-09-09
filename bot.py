import os
import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import asyncio
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
REPEATER_ROLE_ID = 1547134069714845767     # 【新規】リピーターロールのID

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
    
    # 該当ユーザーのこれまでの取引回数をカウント
    cursor.execute("SELECT COUNT(*) FROM history WHERE user_id = ?", (user_id,))
    count = cursor.fetchone()[0]
    
    conn.commit()
    conn.close()
    return count


# ==========================================
# 🎫 新・お問い合わせ用システム（プライベートチャンネル型）
# ==========================================

class InquiryMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 チケット発行（お問い合わせ）", style=discord.ButtonStyle.primary, custom_id="inquiry_open_btn_v2")
    async def open_inquiry(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        
        admin_member = guild.get_member(ADMIN_USER_ID)
        if admin_member:
            overwrites[admin_member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel_name = f"inquiry-{interaction.user.id}"
        
        try:
            inquiry_channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                topic=f"お問い合わせユーザー: {interaction.user.id} ({interaction.user.name})"
            )
        except Exception as e:
            await interaction.followup.send(f"❌ チャンネルの作成に失敗いたしました。権限や設定をご確認ください。\nエラー詳細: `{e}`", ephemeral=True)
            return

        embed = discord.Embed(
            title="🎫 お問い合わせ専用チャンネル",
            description=(
                "こちらはプライベートなお問い合わせチャンネルとなっております。\n"
                "ご不明な点やご質問がございましたら、**そのままこのチャンネルにご記入ください！**\n"
                "管理人が確認次第、こちらにてご返信させていただきます。\n\n"
                "お問い合わせが解決いたしましたら、下のボタンよりチャンネルの終了をお願いいたします。"
            ),
            color=0x3498DB
        )
        
        view = InquiryCloseView()
        await inquiry_channel.send(content=f"{interaction.user.mention} 様、お問い合わせルームを作成いたしました！", embed=embed, view=view)
        await interaction.followup.send(f"✅ お問い合わせ専用の個室を作成いたしました！ 👉 {inquiry_channel.mention}", ephemeral=True)


class InquiryCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 チャンネルを終了する", style=discord.ButtonStyle.danger, custom_id="inquiry_close_btn_v2")
    async def close_inquiry(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("⚠️ **10秒後にこのチャンネルは削除されます！**", ephemeral=False)
        await asyncio.sleep(10)
        try:
            await interaction.channel.delete()
        except Exception:
            pass


# ==========================================
# 🎫 チケット＆個室システムのビュー・モーダル群
# ==========================================

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
                placeholder=placeholder if placeholder else "ご入力ください",
                style=style_type,
                required=True
            )
            self.inputs.append((label, text_input))
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        guild = interaction.guild
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        
        admin_member = guild.get_member(ADMIN_USER_ID)
        if admin_member:
            overwrites[admin_member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel_name = f"ticket-{interaction.user.id}"
        
        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites,
                topic=f"購入者: {interaction.user.id} ({interaction.user.name}) | 商品: {self.item_name} ({self.expected_amount}円)"
            )
        except Exception as e:
            await interaction.followup.send(f"❌ チャンネルの作成に失敗いたしました。権限や設定をご確認ください。\nエラー: `{e}`", ephemeral=True)
            return
        
        details_list = [f"{label}: {input_item.value}" for label, input_item in self.inputs]
        full_details_str = "\n".join(details_list)
        
        # 履歴保存と同時に取引回数を取得
        trade_count = save_history(interaction.user.id, str(interaction.user), self.item_name, self.expected_amount, full_details_str)

        await interaction.followup.send(f"✅ 専用の個室を作成いたしました！ 👉 {ticket_channel.mention}", ephemeral=True)
        
        embed = discord.Embed(
            title="🛒 ご注文誠にありがとうございます！",
            description=(
                f"**ご購入者様:** {interaction.user.mention}\n"
                f"**商品:** {self.item_name} ({self.expected_amount}円)\n\n"
                f"**ご提出いただいたご注文詳細:**\n{full_details_str}\n\n"
                "─────────────────────\n"
                "⚠️ **次のステップへお進みください** ⚠️\n"
                "下の**【🔑 LINEログイン情報を入力する】**ボタンを押して、\n"
                "ツムツム連携されているLINEのメールアドレスとパスワードをご入力ください！"
            ),
            color=0x2ECC71
        )
        
        view = InitialLoginView(buyer_id=interaction.user.id)
        await ticket_channel.send(content=f"{interaction.user.mention} 様、専用取引ルームへようこそ！", embed=embed, view=view)

        # 【リピーターロール付与ロジック】取引回数が2回以上の場合
        if trade_count >= 2:
            repeater_role = guild.get_role(REPEATER_ROLE_ID)
            if repeater_role:
                try:
                    member = guild.get_member(interaction.user.id)
                    if member and repeater_role not in member.roles:
                        await member.add_roles(repeater_role)
                        await ticket_channel.send(f"🎉 おおっと！ {interaction.user.mention} 様は今回で**2回目のご購入**となりますので、専用の【リピーター】ロールを自動付与いたしました！✨ いつもありがとうございます！")
                except Exception as e:
                    print(f"リピーターロール付与エラー: {e}")


class InitialLoginView(discord.ui.View):
    def __init__(self, buyer_id: int):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id

    @discord.ui.button(label="🔑 LINEログイン情報を入力する", style=discord.ButtonStyle.primary, custom_id="ticket_input_login")
    async def open_login_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.buyer_id:
            await interaction.response.send_message("❌ こちらはご購入者ご本人様のみ操作可能なボタンとなっております！", ephemeral=True)
            return
            
        await interaction.response.send_modal(LoginModal(buyer_id=self.buyer_id))


class LoginModal(discord.ui.Modal, title="LINE ログイン情報入力"):
    line_email = discord.ui.TextInput(
        label="LINEメールアドレス",
        placeholder="example@line.com",
        style=discord.TextStyle.short,
        required=True
    )
    line_password = discord.ui.TextInput(
        label="LINEパスワード",
        placeholder="パスワードをご入力ください",
        style=discord.TextStyle.short,
        required=True
    )

    def __init__(self, buyer_id: int):
        super().__init__()
        self.buyer_id = buyer_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        embed = discord.Embed(
            title="✅ ご購入手続きが完了いたしました！",
            description=(
                "ログイン情報のご提出、誠にありがとうございます！\n"
                "管理人が確認次第、作業を開始いたしますので今しばらくお待ちくださいませ。\n\n"
                "**【ご提出いただいたログイン情報】**\n"
                f"📧 **メールアドレス:** `{self.line_email.value}`\n"
                f"🔑 **パスワード:** `{self.line_password.value}`"
            ),
            color=0x3498DB
        )
        
        view = TicketRoomView(buyer_id=self.buyer_id)
        await interaction.channel.send(content=f"{interaction.user.mention} お手続き完了でございます！", embed=embed, view=view)


class TicketRoomView(discord.ui.View):
    def __init__(self, buyer_id: int):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id
        self.mentioned_count = 0

    @discord.ui.button(label="🔔 管理人を呼ぶ", style=discord.ButtonStyle.primary, custom_id="ticket_mention_admin")
    async def mention_admin(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.mentioned_count >= 1:
            await interaction.response.send_message("❌ こちらのボタンは1回のみご使用いただけます。管理人が確認するまでお待ちくださいませ！", ephemeral=True)
            return
        
        self.mentioned_count += 1
        await interaction.channel.send(f"🚨 お客様より管理人の呼び出し要請がございました！ <@{ADMIN_USER_ID}>")
        await interaction.response.send_message("✅ 管理人へ通知を送信いたしました！", ephemeral=True)

    @discord.ui.button(label="✅ 作業完了（管理人専用）", style=discord.ButtonStyle.success, custom_id="ticket_complete_trade")
    async def complete_trade(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != ADMIN_USER_ID and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ お客様にはこちらのボタンを押す権限がございません！", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="🎉 代行作業が完了いたしました！",
            description=(
                "お疲れ様でございました！これにて全ての代行作業は終了となります。\n"
                "よろしければ、下の**【実績を入力する】**ボタンよりご感想をお聞かせください！\n"
                "（ご感想を入力いただきますと、自動でロールが付与されチャンネルを閉じることが可能になります！）"
            ),
            color=0xF1C40F
        )
        
        view = AfterTradeView(buyer_id=self.buyer_id)
        await interaction.channel.send(embed=embed, view=view)
        await interaction.response.send_message("✅ 作業完了メッセージを送信いたしました！", ephemeral=True)


# 5. PIN入力要請時のボタンビュー【修正：誰でも押せるように制限を撤廃】
class PinEntryView(discord.ui.View):
    def __init__(self, buyer_id: int):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id

    @discord.ui.button(label="📌 PINを入力しました", style=discord.ButtonStyle.success, custom_id="ticket_pin_submitted")
    async def pin_submitted(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ※ プライベートチャンネルでの作業者（あなた）やアカウント所有者が誰でも押せるように本人チェックを解除！
        embed = discord.Embed(
            title="❓ 確認",
            description=(
                "**本当に入力はお済みでしょうか？**\n"
                "⚠️ **※未入力の場合、代行処理が失敗する可能性がございます！**"
            ),
            color=0xE74C3C
        )
        view = PinConfirmYesNoView(buyer_id=self.buyer_id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# 6. PIN入力確認（はい／いいえ）のビュー【修正：こちらも誰でも確認できるように制限を解除】
class PinConfirmYesNoView(discord.ui.View):
    def __init__(self, buyer_id: int):
        super().__init__(timeout=None)
        self.buyer_id = buyer_id

    @discord.ui.button(label="はい（入力済み）", style=discord.ButtonStyle.danger)
    async def confirm_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="✅ 確認が完了いたしました！", embed=None, view=None)
        
        embed = discord.Embed(
            title="✅ PIN入力完了",
            description="PINコードの入力完了を確認いたしました。少々お待ちくださいませ！",
            color=0x2ECC71
        )
        await interaction.channel.send(content=f"<@{self.buyer_id}>", embed=embed)

    @discord.ui.button(label="いいえ（戻る）", style=discord.ButtonStyle.secondary)
    async def confirm_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="↩️ キャンセルいたしました。LINE側でPINコードをご入力後、再度ボタンを押してください！", embed=None, view=None)


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
            await interaction.response.send_message("❌ お客様にはこちらのボタンを押す権限がございません！", ephemeral=True)
            return
        
        try:
            buyer = interaction.guild.get_member(self.buyer_id)
            if buyer:
                await buyer.send("📢 専用取引チャンネルは管理者によりクローズされました。ご利用誠にありがとうございました！")
        except:
            pass
        
        await interaction.channel.delete()


class ReviewModal(discord.ui.Modal, title="お取引の感想・実績入力"):
    review_text = discord.ui.TextInput(
        label="ご感想・レビュー",
        placeholder="対応のスピードやご感想を自由にお書きください！",
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
                title="🌟 新しいお客様の実績・ご感想でございます！",
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
            title="✨ 実績のご協力、誠にありがとうございます！",
            description="ロールの付与が完了いたしました！下のボタンを押しますと、このチャンネルをいつでも安全に削除することが可能です。",
            color=0x3498DB
        )
        view = FinalCloseView()
        await interaction.channel.send(embed=embed, view=view)
        await interaction.followup.send("✅ 実績を送信し、ロールを付与いたしました！ご協力誠にありがとうございます！", ephemeral=True)


class FinalCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="チャンネルを閉じる（削除）", style=discord.ButtonStyle.danger, emoji="🚪", custom_id="ticket_final_close")
    async def close_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🚪 チャンネルを削除しております...", ephemeral=True)
        try:
            buyer = interaction.user
            await buyer.send("📢 お取引チャンネルが閉じられました。ご利用誠にありがとうございました！")
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
            ("ご希望コイン数", "例: 1,000,000", False),
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
            ("ご希望金額・詳細", "例: 指定ツムで回数分割コイン獲得", True),
            ("PayPayリンク", "https://pay.paypay.ne.jp/...", True)
        ]
        await interaction.response.send_modal(OrderModal("高品質コイン", 1700, fields))


# --- 【新規】管理人以外のスラッシュコマンド実行ブロック用グローバルチェック ---
@bot.tree.check
async def global_admin_check(interaction: discord.Interaction):
    # 管理人（ADMIN_USER_ID）以外からのコマンド実行はすべてブロック！
    if interaction.user.id != ADMIN_USER_ID:
        raise app_commands.CheckFailure("Not authorized admin")
    return True

# エラーハンドラー：権限がない人がコマンドを叩いた時に「権限がありません」と優しく（冷酷に）返す
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        msg = "❌ 権限がありません！こちらのコマンドは管理者専用となっております。"
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
    else:
        print(f"予期せぬコマンドエラーが発生しました: {error}")


# --- Bot起動処理 ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}!")
    
    bot.add_view(ShopMainView())
    bot.add_view(FinalCloseView())
    bot.add_view(InquiryMainView())
    bot.add_view(InquiryCloseView())
    
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"[{len(synced)}] 個のコマンドを同期完了！")
    except Exception as e:
        print(f"同期エラー: {e}")


# ==========================================
# コマンド群（すべてADMIN_USER_ID以外の実行は上で弾かれます）
# ==========================================

@bot.tree.command(name="shop_enter", description="【管理者専用】ツムツml自動代行のショップパネルを設置します")
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
    await interaction.response.send_message("✅ ショップパネルを設置いたしました！", ephemeral=True)


@bot.tree.command(name="inquiry_enter", description="【管理者専用】お問い合わせ用のチケットパネルを設置します")
async def create_inquiry(interaction: discord.Interaction):
    embed = discord.Embed(
        title="カテゴリー：お問い合わせ",
        description="下のボタンを押すことで、専用のお問い合わせチャンネルが作成されます。\nご不明な点や問題がございましたら、お気軽にお問い合わせください！",
        color=0x9B59B6
    )
    view = InquiryMainView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ お問い合わせパネルを設置いたしました！", ephemeral=True)


@bot.tree.command(name="processing", description="【管理者専用】作業開始（処理中）の案内を投稿します")
async def processing_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="**対応を開始いたしました。**",
        description="数分お待ちくださいませ。",
        color=0x9B59B6
    )
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ 処理中テキストを出力いたしました！", ephemeral=True)


@bot.tree.command(name="pin_entry", description="【管理者専用】お客さんに4桁のPINコード入力を要請します")
@app_commands.describe(pin="4桁のPINコード")
async def pin_entry_cmd(interaction: discord.Interaction, pin: str):
    buyer_id = None
    if interaction.channel.topic and "購入者: " in interaction.channel.topic:
        try:
            buyer_id = int(interaction.channel.topic.split("購入者: ")[1].split(" ")[0])
        except:
            pass
    
    if not buyer_id:
        buyer_id = interaction.user.id

    embed = discord.Embed(
        title="🔐 PINコードの入力をお願いいたします",
        description=(
            f"こちらのPINコードをご入力ください。\n\n"
            f"👉 **PINコード: `{pin}`**\n\n"
            "ご入力が完了いたしましたら、下の**【📌 PINを入力しました】**ボタンを押してください！"
        ),
        color=0xE67E22
    )
    
    view = PinEntryView(buyer_id=buyer_id)
    await interaction.channel.send(content=f"<@{buyer_id}> PINコード入力の要請でございます！", embed=embed, view=view)
    await interaction.response.send_message("✅ PINコード入力要請を投稿いたしました！", ephemeral=True)


@bot.tree.command(name="history", description="【管理者専用】取引履歴を確認します（自分だけに表示）")
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

    Tuple_res = await interaction.response.send_message(embed=embed, ephemeral=True)

# === Webサーバーをバックグラウンド起動してからBotを開始！ ===
keep_alive()
bot.run(TOKEN)
