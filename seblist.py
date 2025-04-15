import asyncio
from datetime import datetime, timedelta
import sys
import os
import threading
import logging

from telethon import TelegramClient, events
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import Flask

# === KONFIGURASI TELEGRAM ===
api_id = 18973385  # Ganti dengan api_id milikmu (dari my.telegram.org)
api_hash = '507fb19ac5a92c7955ad0260b62830d6'  # Ganti dengan api_hash milikmu
client = TelegramClient("user_session", api_id, api_hash)

# === SETUP LOGGER ===
logging.basicConfig(filename='bot.log', level=logging.INFO,
                    format='[%(asctime)s] [%(levelname)s] %(message)s')

# === SCHEDULER ===
scheduler = AsyncIOScheduler()

# === DATA GLOBAL ===
blacklisted_groups = set()
job_data = {}
delay_setting = {}
MASA_AKTIF = datetime(2030, 12, 31)
pesan_simpan = {}   # key: user_id, value: pesan terbaru
preset_pesan = {}   # key: user_id, value: {nama_preset: isi_pesan}
usage_stats = {}    # key: user_id, value: jumlah pesan yang berhasil dikirim

HARI_MAPPING = {
    "senin": "monday", "selasa": "tuesday", "rabu": "wednesday",
    "kamis": "thursday", "jumat": "friday", "sabtu": "saturday", "minggu": "sunday"
}

# === FUNCTION UNTUK MENGHITUNG STATISTIK PENGGUNA ===
def update_usage(user_id, count):
    usage_stats[user_id] = usage_stats.get(user_id, 0) + count

# === FUNCTION UNTUK MELAKUKAN FORWARDING PESAN ===
async def forward_job(user_id, mode, source, message_id_or_text, jumlah_grup, durasi_jam: float, jumlah_pesan):
    start = datetime.now()
    end = start + timedelta(hours=durasi_jam)
    jeda_batch = delay_setting.get(user_id, 5)

    now = datetime.now()
    next_reset = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    harian_counter = 0  
    total_counter = 0  

    info_msg = f"[{now:%H:%M:%S}] 💖 Mulai meneruskan pesan selama {durasi_jam:.2f} jam."
    print(info_msg)
    logging.info(info_msg)
    try:
        await client.send_message(user_id, f"⏱💗 Sedang meneruskan pesan...\nDurasi: {durasi_jam:.2f} jam\nTarget harian: {jumlah_pesan} pesan.")
    except Exception as e:
        logging.error(f"💔 Error mengirim info ke {user_id}: {e}")

    while datetime.now() < end:
        if datetime.now() >= next_reset:
            harian_counter = 0
            next_reset += timedelta(days=1)
            reset_msg = f"[{datetime.now():%H:%M:%S}] 💖 Reset harian: pengiriman akan dilanjutkan besok!"
            print(reset_msg)
            logging.info(reset_msg)

        counter = 0  
        async for dialog in client.iter_dialogs():
            if datetime.now() >= end or harian_counter >= jumlah_pesan:
                break
            # Cek jika dialog berupa grup dan tidak termasuk blacklist
            if not dialog.is_group or dialog.name in blacklisted_groups:
                continue
            try:
                if mode == "forward":
                    msg = await client.get_messages(source, ids=int(message_id_or_text))
                    if msg:
                        await client.forward_messages(dialog.id, msg.id, from_peer=source)
                else:
                    await client.send_message(dialog.id, message_id_or_text, link_preview=True)

                counter += 1
                harian_counter += 1
                total_counter += 1
                update_usage(user_id, 1)
                log_msg = f"[{datetime.now():%H:%M:%S}] 💖✅ Dikirim ke grup: {dialog.name}"
                print(log_msg)
                logging.info(log_msg)

                if counter >= jumlah_grup or harian_counter >= jumlah_pesan:
                    break

            except Exception as e:
                error_msg = f"[{datetime.now():%H:%M:%S}] 💔❌ Gagal kirim ke {dialog.name}: {e}"
                print(error_msg)
                logging.error(error_msg)
                continue

        if harian_counter >= jumlah_pesan:
            notif = f"🎯 Target harian {jumlah_pesan} pesan tercapai!\nBot akan lanjut besok pada jam yang sama, sayang 💗"
            info_notif = f"[{datetime.now():%H:%M:%S}] 💖 {notif}"
            print(info_notif)
            logging.info(info_notif)
            try:
                await client.send_message(user_id, notif)
            except Exception as e:
                logging.error(f"💔 Error mengirim notifikasi ke {user_id}: {e}")
            sleep_seconds = (next_reset - datetime.now()).total_seconds()
            await asyncio.sleep(sleep_seconds)
        else:
            batch_msg = f"[{datetime.now():%H:%M:%S}] 💖 Batch {counter} grup selesai. Jeda {jeda_batch} detik..."
            print(batch_msg)
            logging.info(batch_msg)
            await asyncio.sleep(jeda_batch)

    selesai = f"✅ Forward selesai!\nTotal terkirim ke {total_counter} grup selama {durasi_jam:.2f} jam."
    selesai_msg = f"[{datetime.now():%H:%M:%S}] 💖 {selesai}"
    print(selesai_msg)
    logging.info(selesai_msg)
    try:
        await client.send_message(user_id, selesai)
    except Exception as e:
        logging.error(f"💔 Error mengirim pesan selesai ke {user_id}: {e}")

# === PERINTAH BOT ===

@client.on(events.NewMessage(pattern='/scheduleforward'))
async def schedule_cmd(event):
    args = event.message.raw_text.split(maxsplit=2)
    if len(args) < 3:
        return await event.respond("❌ Format salah:\n/scheduleforward mode pesan/sumber jumlah_grup durasi jeda jumlah_pesan hari,jam jam:menit")
    try:
        mode = args[1]
        sisa = args[2].rsplit(" ", 6)
        if len(sisa) != 7:
            return await event.respond("❌ Format tidak sesuai. Pastikan argumen lengkap!")
        isi_pesan, jumlah, durasi, jeda, jumlah_pesan, hari_str, waktu = sisa
        jumlah = int(jumlah)
        durasi = int(durasi)
        jeda = int(jeda)
        jumlah_pesan = int(jumlah_pesan)
        jam, menit = map(int, waktu.split(":"))
        hari_list = [HARI_MAPPING.get(h.lower()) for h in hari_str.split(",")]

        if None in hari_list:
            return await event.respond("❌ Terdapat nama hari yang tidak valid. Gunakan: senin,selasa,...,minggu.")

        for hari_eng in hari_list:
            job_id = f"{event.sender_id}{hari_eng}{int(datetime.now().timestamp())}"
            job_data[job_id] = {
                "user": event.sender_id, "mode": mode, "source": "",
                "message": isi_pesan, "jumlah": jumlah,
                "durasi": durasi, "jeda": jeda, "jumlah_pesan": jumlah_pesan
            }
            delay_setting[event.sender_id] = jeda
            scheduler.add_job(
                forward_job,
                trigger=CronTrigger(day_of_week=hari_eng, hour=jam, minute=menit),
                args=[event.sender_id, mode, "", isi_pesan, jumlah, durasi, jumlah_pesan],
                id=job_id
            )

        daftar_hari = ", ".join([h.title() for h in hari_str.split(",")])
        await event.respond(f"💗 Jadwal forward berhasil ditambahkan untuk hari {daftar_hari} pukul {waktu}!")
    except Exception as e:
        err_msg = f"💔 Error: {e}"
        logging.error(err_msg)
        await event.respond(err_msg)

@client.on(events.NewMessage(pattern='/forward'))
async def forward_sekarang(event):
    args = event.message.raw_text.split(maxsplit=7)
    if len(args) < 7:
        return await event.respond("❌ Format salah:\n/forward mode sumber/id/isipesan jumlah_grup jeda durasi jumlah_pesan\nContoh:\n/forward forward @channel 5 12345 5 2 300\natau\n/forward text \"Halo semua!\" 10 5 3 300")
    try:
        mode = args[1]
        if mode == "forward":
            source = args[2]
            jumlah = int(args[3])
            message_id = int(args[4])
            jeda_batch = int(args[5])
            durasi = int(args[6])
            jumlah_pesan = int(args[7]) if len(args) >= 8 else 300
            delay_setting[event.sender_id] = jeda_batch
            await forward_job(event.sender_id, mode, source, message_id, jumlah, durasi, jumlah_pesan)
        elif mode == "text":
            text = args[2]
            jumlah = int(args[3])
            jeda_batch = int(args[4])
            durasi = int(args[5])
            jumlah_pesan = int(args[6]) if len(args) >= 7 else 300
            delay_setting[event.sender_id] = jeda_batch
            pesan_simpan[event.sender_id] = text
            await forward_job(event.sender_id, mode, "", text, jumlah, durasi, jumlah_pesan)
        else:
            await event.respond("❌ Mode harus 'forward' atau 'text'")
    except Exception as e:
        err_msg = f"💔 Error: {e}"
        logging.error(err_msg)
        await event.respond(err_msg)

@client.on(events.NewMessage(pattern='/setdelay'))
async def set_delay(event):
    try:
        delay = int(event.message.raw_text.split()[1])
        delay_setting[event.sender_id] = delay
        await event.respond(f"💗 Jeda antar batch diset ke {delay} detik!")
    except Exception as e:
        logging.error(f"💔 Error pada /setdelay: {e}")
        await event.respond("❌ Gunakan: /setdelay <detik>")

@client.on(events.NewMessage(pattern='/review'))
async def review_jobs(event):
    teks = "💗== Jadwal Aktif ==\n"
    if not job_data:
        teks += "Tidak ada jadwal."
    else:
        for job_id, info in job_data.items():
            teks += f"- ID: {job_id}\n  Mode: {info['mode']}\n  Grup: {info['jumlah']}\n  Durasi: {info['durasi']} jam\n"
    await event.respond(teks)

@client.on(events.NewMessage(pattern='/deletejob'))
async def delete_job(event):
    try:
        job_id = event.message.raw_text.split()[1]
        scheduler.remove_job(job_id)
        job_data.pop(job_id, None)
        await event.respond("💗 Jadwal berhasil dihapus!")
    except Exception as e:
        logging.error(f"💔 Error pada /deletejob: {e}")
        await event.respond("❌ Gagal menghapus. Pastikan ID yang dimasukkan benar.")

# Command untuk menghentikan semua job forward milik pengguna tertentu
@client.on(events.NewMessage(pattern='/stopforward'))
async def stop_forward(event):
    user_id = event.sender_id
    removed = []
    for job in scheduler.get_jobs():
        if str(user_id) in job.id:
            try:
                scheduler.remove_job(job.id)
                job_data.pop(job.id, None)
                removed.append(job.id)
            except Exception as e:
                logging.error(f"💔 Error menghapus job {job.id}: {e}")
    if removed:
        await event.respond(f"💗 Semua job forward untuk Anda telah dihapus: {', '.join(removed)}")
    else:
        await event.respond("❌ Tidak ditemukan job forward untuk Anda.")

@client.on(events.NewMessage(pattern='/blacklist_add'))
async def add_blacklist(event):
    try:
        nama = " ".join(event.message.raw_text.split()[1:])
        blacklisted_groups.add(nama)
        await event.respond(f"💗 '{nama}' berhasil masuk ke blacklist!")
    except Exception as e:
        logging.error(f"💔 Error pada /blacklist_add: {e}")
        await event.respond("❌ Format salah. Gunakan: /blacklist_add <nama grup>")

@client.on(events.NewMessage(pattern='/blacklist_remove'))
async def remove_blacklist(event):
    try:
        nama = " ".join(event.message.raw_text.split()[1:])
        blacklisted_groups.discard(nama)
        await event.respond(f"💗 '{nama}' telah dihapus dari blacklist!")
    except Exception as e:
        logging.error(f"💔 Error pada /blacklist_remove: {e}")
        await event.respond("❌ Format salah. Gunakan: /blacklist_remove <nama grup>")

@client.on(events.NewMessage(pattern='/list_blacklist'))
async def list_blacklist(event):
    if not blacklisted_groups:
        await event.respond("💗 Blacklist kosong!")
    else:
        teks = "💗== Grup dalam blacklist ==\n" + "\n".join(blacklisted_groups)
        await event.respond(teks)

@client.on(events.NewMessage(pattern='/status'))
async def cek_status(event):
    now = datetime.now()
    sisa = (MASA_AKTIF - now).days
    tanggal_akhir = MASA_AKTIF.strftime('%d %B %Y')
    await event.respond(
        f"💖 Masa aktif tersisa: {sisa} hari\n💖 Userbot aktif sampai: {tanggal_akhir}"
    )

@client.on(events.NewMessage(pattern='/review_pesan'))
async def review_pesan(event):
    pesan = pesan_simpan.get(event.sender_id)
    if not pesan:
        await event.respond("💗 Belum ada pesan yang disimpan.")
    else:
        await event.respond(f"💗== Isi Pesan Saat Ini ==\n{pesan}")

@client.on(events.NewMessage(pattern='/ubah_pesan'))
async def ubah_pesan(event):
    try:
        teks = event.message.raw_text.split(" ", maxsplit=1)[1]
        pesan_simpan[event.sender_id] = teks
        await event.respond("💗 Isi pesan berhasil diubah!")
    except Exception as e:
        logging.error(f"💔 Error pada /ubah_pesan: {e}")
        await event.respond("❌ Format salah. Gunakan:\n/ubah_pesan <pesan_baru>")

@client.on(events.NewMessage(pattern='/simpan_preset'))
async def simpan_preset(event):
    try:
        user_id = event.sender_id
        parts = event.message.raw_text.split(" ", maxsplit=2)
        if len(parts) < 3:
            return await event.respond("❌ Format salah. Gunakan:\n/simpan_preset <nama> <pesan>")
        _, nama, pesan = parts
        preset_pesan.setdefault(user_id, {})[nama] = pesan
        await event.respond(f"💗 Preset '{nama}' berhasil disimpan!")
    except Exception as e:
        logging.error(f"💔 Error pada /simpan_preset: {e}")
        await event.respond("❌ Format salah. Gunakan:\n/simpan_preset <nama> <pesan>")

@client.on(events.NewMessage(pattern='/pakai_preset'))
async def pakai_preset(event):
    try:
        user_id = event.sender_id
        nama = event.message.raw_text.split(" ", maxsplit=1)[1]
        pesan = preset_pesan.get(user_id, {}).get(nama)
        if not pesan:
            return await event.respond(f"❌ Tidak ada preset dengan nama '{nama}'!")
        pesan_simpan[user_id] = pesan
        await event.respond(f"💗 Preset '{nama}' dipilih:\n\n{pesan}")
    except Exception as e:
        logging.error(f"💔 Error pada /pakai_preset: {e}")
        await event.respond("❌ Format salah. Gunakan:\n/pakai_preset <nama>")

@client.on(events.NewMessage(pattern='/list_preset'))
async def list_preset(event):
    user_id = event.sender_id
    daftar = preset_pesan.get(user_id, {})
    if not daftar:
        return await event.respond("💗 Belum ada preset.")
    teks = "💗== Daftar Preset ==\n" + "\n".join(f"- {nama}" for nama in daftar)
    await event.respond(teks)

@client.on(events.NewMessage(pattern='/edit_preset'))
async def edit_preset(event):
    try:
        user_id = event.sender_id
        parts = event.message.raw_text.split(" ", maxsplit=2)
        if len(parts) < 3:
            return await event.respond("❌ Format salah. Gunakan:\n/edit_preset <nama> <pesan_baru>")
        _, nama, pesan_baru = parts
        if nama not in preset_pesan.get(user_id, {}):
            return await event.respond(f"❌ Tidak ada preset dengan nama '{nama}'!")
        preset_pesan[user_id][nama] = pesan_baru
        await event.respond(f"💗 Preset '{nama}' berhasil diubah!")
    except Exception as e:
        logging.error(f"💔 Error pada /edit_preset: {e}")
        await event.respond("❌ Format salah. Gunakan:\n/edit_preset <nama> <pesan_baru>")

@client.on(events.NewMessage(pattern='/hapus_preset'))
async def hapus_preset(event):
    try:
        user_id = event.sender_id
        nama = event.message.raw_text.split(" ", maxsplit=1)[1]
        if nama in preset_pesan.get(user_id, {}):
            del preset_pesan[user_id][nama]
            await event.respond(f"💗 Preset '{nama}' berhasil dihapus!")
        else:
            await event.respond(f"❌ Preset '{nama}' tidak ditemukan.")
    except Exception as e:
        logging.error(f"💔 Error pada /hapus_preset: {e}")
        await event.respond("❌ Format salah. Gunakan:\n/hapus_preset <nama>")

@client.on(events.NewMessage(pattern='/ping'))
async def ping(event):
    await event.respond("💖 Bot aktif dan siap melayani!")

# Command untuk restart bot
@client.on(events.NewMessage(pattern='/restart'))
async def restart(event):
    await event.respond("💗 Bot akan restart...")
    logging.info("💖 Restarting bot upon command...")
    os.execv(sys.executable, [sys.executable] + sys.argv)

@client.on(events.NewMessage(pattern='/log'))
async def log(event):
    try:
        with open("bot.log", "r") as log_file:
            logs = log_file.read()
            if len(logs) > 4000:
                logs = logs[-4000:]
            await event.respond(f"📜💗 Log Terbaru:\n{logs}")
    except FileNotFoundError:
        await event.respond("❌ Log tidak ditemukan.")

@client.on(events.NewMessage(pattern='/feedback'))
async def feedback(event):
    try:
        feedback_message = event.message.raw_text.split(maxsplit=1)[1]
        # Kirim feedback ke admin (ganti admin_chat_id sesuai kebutuhan)
        admin_chat_id = 1538087933
        await client.send_message(admin_chat_id, f"💗 Feedback dari {event.sender_id}:\n{feedback_message}")
        await event.respond("💗 Terima kasih atas feedback Anda!")
    except IndexError:
        await event.respond("❌ Format salah! Gunakan: /feedback <pesan>")

@client.on(events.NewMessage(pattern='/help'))
async def help_cmd(event):
    teks = """
✨💖 PANDUAN USERBOT HEARTIE 💖✨

Hai, sayang! Aku Heartie, userbot-mu yang siap membantu menyebarkan pesan cinta ke semua grup-grup favoritmu. Berikut daftar perintah yang bisa kamu gunakan:

============================
1. /forward  
   Kirim pesan langsung ke grup.  
   - Mode forward (dari channel):  
     /forward forward @namachannel jumlah_grup id_pesan jeda detik durasi jam jumlah_pesan_perhari  
     Contoh: /forward forward @usnchannel 50 27 5 3 300  
   - Mode text (kirim teks langsung):  
     /forward text "Halo semua!" jumlah_grup jeda detik durasi jam jumlah_pesan_perhari  
     Contoh: /forward text "Halo semua!" 10 5 3 300  

============================
2. */scheduleforward*  
   Jadwalkan pesan mingguan otomatis.  
   *Format:*  
   /scheduleforward mode pesan/sumber jumlah_grup durasi jeda jumlah_pesan hari1,day2 jam:menit  
   *Contoh:*  
   /scheduleforward forward @usnchannel 20 2 5 300 senin,jumat 08:00  
   /scheduleforward text "Halo dari bot!" 30 3 5 300 selasa,rabu 10:00  

============================
3. Manajemen Preset & Pesan  
   - /review_pesan — Lihat pesan default  
   - /ubah_pesan <pesan_baru> — Ubah pesan default  
   - /simpan_preset <nama> <pesan> — Simpan preset pesan  
   - /pakai_preset <nama> — Pilih preset sebagai pesan default  
   - /list_preset — Tampilkan daftar preset  
   - /edit_preset <nama> <pesan_baru> — Edit preset pesan  
   - /hapus_preset <nama> — Hapus preset  

============================
4. Pengaturan Job Forward & Delay  
   - /review — Tampilkan jadwal aktif  
   - /deletejob <id> — Hapus jadwal forward  
   - /setdelay <detik> — Atur jeda antar batch kirim  
   - /stopforward — Hentikan semua job forward aktif kamu

============================
5. Blacklist Grup
   - /blacklist_add <nama grup> — Tambahkan grup ke blacklist  
   - /blacklist_remove <nama grup> — Hapus grup dari blacklist  
   - /list_blacklist — Lihat daftar grup dalam blacklist  

============================
6. Info & Lain-lain  
   - /status — Cek masa aktif userbot  
   - /ping — Periksa apakah bot aktif  
   - /log — Tampilkan log aktivitas bot  
   - /feedback <pesan> — Kirim feedback ke pengembang  
   - /stats — Lihat statistik penggunaan forward  
   - /restart — Restart bot  

============================
✨ Cara mendapatkan ID pesan channel:  
Klik kanan bagian kosong (atau tap lama) pada pesan di channel → Salin link.  
Misal, jika linknya https://t.me/usnchannel/19 maka id pesan adalah 19.

Selamat mencoba dan semoga hari-harimu penuh cinta! 💗 Kalau masih ada yang bingung bisa chat pengembangku (zero) ya!
"""
    await event.respond(teks)

# Command untuk menampilkan statistik penggunaan
@client.on(events.NewMessage(pattern='/stats'))
async def stats(event):
    user_id = event.sender_id
    total = usage_stats.get(user_id, 0)
    await event.respond(f"💗 Statistik Pengguna (ID: {user_id}):\nTotal pesan berhasil dikirim: {total}")

# === PENGECEKAN LISENSI ===
async def cek_lisensi():
    if datetime.now() > MASA_AKTIF:
        logging.error("💔 Lisensi expired. Bot dihentikan.")
        sys.exit("💔 Lisensi expired.")

# === SETUP FLASK UNTUK KEEP ALIVE (misal untuk Railway / UptimeRobot) ===
app = Flask(__name__)

@app.route('/')
def home():
    return "💗 Heartie Bot is alive!"

def keep_alive():
    app.run(host="0.0.0.0", port=8000)

# Jalankan server Flask di thread terpisah agar aplikasi tetap aktif
threading.Thread(target=keep_alive).start()

# === JALANKAN BOT ===
async def main():
    await client.start()
    scheduler.start()  # Scheduler berjalan di event loop
    await cek_lisensi()
    me = await client.get_me()
    welcome_msg = f"💖 Bot aktif, kamu masuk sebagai {me.first_name}. Menunggu perintahmu, sayang!"
    print(welcome_msg)
    logging.info(welcome_msg)
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())