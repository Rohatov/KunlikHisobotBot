/**
 * Google Sheets ichidagi "Hisobotni yuborish" tugmasi/havolasi uchun skript.
 *
 * Bu Telegram botdagi /report buyrug'i bilan bir xil natijani beradi:
 * ikkita varaqni (Savdo, Qoldiq) PDF qilib, Telegram guruh/kanaliga yuboradi.
 * Python bot bilan hech qanday bog'lanish yo'q — bu mustaqil skript, chunki
 * Apps Script serverga to'g'ridan-to'g'ri ulana olmaydi. Shuning uchun
 * eksport parametrlari va fayl nomlash Python tomonidagi
 * app/sheets_service.py va app/pdf_service.py bilan qo'lda mos qilib
 * qo'yilgan — ikkalasi ham bir xil natija beradi.
 *
 * IKKI XIL ISHLATISH USULI:
 *
 * A) Faqat tahrirlash (Editor) huquqi borlar uchun — Drawing + Assign script:
 *    "sendReportToTelegram" funksiyasi jadval ichidagi chizmaga biriktiriladi.
 *    KAMChilik: faqat Editor huquqi bor va shaxsan avtorizatsiyadan o'tgan
 *    odam ishlata oladi — Viewer (faqat ko'ruvchi) buni ishga tushira olmaydi.
 *
 * B) Ko'ruvchilar (Viewer) ham foydalana oladigan usul — Web App:
 *    "doGet" funksiyasi Web App sifatida deploy qilinadi (Execute as: Me,
 *    Who has access: Anyone). Bu holda skript doim SIZNING (owner)
 *    nomingizdan ishlaydi — hech kim alohida avtorizatsiyadan o'tmaydi,
 *    shunchaki havolani (link) bosadi. Jadvalga rasm qo'yib, o'sha rasmga
 *    ushbu Web App havolasini bog'lab qo'ying.
 *
 * To'liq qadamlar README.md faylida ("Hisobotni yuborish" bo'limi).
 */

/** Editor jadval ichidan chizma (drawing) orqali ishga tushiradi. */
function sendReportToTelegram() {
  const result = generateAndSendReport_();
  SpreadsheetApp.getUi().alert(result.message);
}

/**
 * Web App sifatida deploy qilinganda ishlaydi (Execute as: Me,
 * Who has access: Anyone). Havolani bosgan har bir kishi uchun
 * (Viewer bo'lsa ham) natija shu sahifada ko'rinadi.
 */
function doGet(e) {
  const result = generateAndSendReport_();
  const color = result.ok ? '#188038' : '#c5221f';
  const title = result.ok ? "✅ Hisobot yuborildi" : '❌ Xatolik yuz berdi';
  const safeMessage = result.message.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const html =
    '<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<style>body{font-family:sans-serif;text-align:center;padding-top:60px;background:#f5f5f5;margin:0}' +
    'h2{color:' + color + '}' +
    'pre{white-space:pre-wrap;text-align:left;display:inline-block;background:#fff;padding:16px 24px;' +
    'border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,0.15)}</style></head>' +
    '<body><h2>' + title + '</h2><pre>' + safeMessage + '</pre></body></html>';

  return HtmlService.createHtmlOutput(html).setTitle('Hisobot');
}

/** Ikkala yo'l ham shu umumiy funksiyani chaqiradi — mantiq takrorlanmaydi. */
function generateAndSendReport_() {
  const lock = LockService.getScriptLock();

  if (!lock.tryLock(5000)) {
    return { ok: false, message: "⚠️ Hisobot allaqachon yuborilyapti. Birozdan so'ng qayta urinib ko'ring." };
  }

  try {
    const props = PropertiesService.getScriptProperties();
    const botToken = props.getProperty('TELEGRAM_BOT_TOKEN');
    const chatId = props.getProperty('TELEGRAM_CHAT_ID');
    const worksheet1Id = props.getProperty('WORKSHEET_1_ID');
    const worksheet2Id = props.getProperty('WORKSHEET_2_ID');

    if (!botToken || !chatId || !worksheet1Id || !worksheet2Id) {
      return {
        ok: false,
        message:
          "Sozlamalar to'liq emas. Project Settings > Script Properties bo'limida " +
          "TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WORKSHEET_1_ID, WORKSHEET_2_ID borligini tekshiring.",
      };
    }

    const spreadsheetId = SpreadsheetApp.getActiveSpreadsheet().getId();
    const dateStr = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');

    const worksheets = [
      { sheetId: worksheet1Id, slug: 'savdo' },
      { sheetId: worksheet2Id, slug: 'qoldiq' },
    ];

    const lines = [];
    let allOk = true;
    for (const ws of worksheets) {
      try {
        const pdfBlob = exportSheetToPdf_(spreadsheetId, ws.sheetId, ws.slug, dateStr);
        sendDocumentToTelegram_(botToken, chatId, pdfBlob);
        lines.push('✅ ' + ws.slug + ': muvaffaqiyatli yuborildi');
      } catch (err) {
        allOk = false;
        lines.push('❌ ' + ws.slug + ': ' + err.message);
      }
    }

    return { ok: allOk, message: lines.join('\n') };
  } finally {
    lock.releaseLock();
  }
}

/** Bitta varaqni (gid orqali) PDF sifatida eksport qiladi. */
function exportSheetToPdf_(spreadsheetId, sheetId, slug, dateStr) {
  const params = {
    format: 'pdf',
    gid: sheetId,
    portrait: 'true',
    size: 'A4',
    fitw: 'true',
    gridlines: 'true',
    printtitle: 'false',
    sheetnames: 'false',
    pagenumbers: 'false',
    top_margin: '0.50',
    bottom_margin: '0.50',
    left_margin: '0.50',
    right_margin: '0.50',
  };
  const query = Object.keys(params)
    .map((key) => key + '=' + encodeURIComponent(params[key]))
    .join('&');
  const url = 'https://docs.google.com/spreadsheets/d/' + spreadsheetId + '/export?' + query;

  const response = UrlFetchApp.fetch(url, {
    headers: { Authorization: 'Bearer ' + ScriptApp.getOAuthToken() },
    muteHttpExceptions: true,
  });

  if (response.getResponseCode() !== 200) {
    throw new Error('PDF eksport qilinmadi (HTTP ' + response.getResponseCode() + ')');
  }

  return response.getBlob().setName(slug + '_' + dateStr + '.pdf');
}

/** Tayyor PDF blobni Telegramga hujjat sifatida (captionsiz) yuboradi. */
function sendDocumentToTelegram_(botToken, chatId, pdfBlob) {
  const url = 'https://api.telegram.org/bot' + botToken + '/sendDocument';
  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    payload: {
      chat_id: chatId,
      document: pdfBlob,
    },
    muteHttpExceptions: true,
  });

  const result = JSON.parse(response.getContentText());
  if (!result.ok) {
    throw new Error(result.description || 'Telegram API xatosi');
  }
}
