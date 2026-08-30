/**
 * Google Sheets ichidagi "Hisobotni yuborish" tugmasi uchun skript.
 *
 * Bu Telegram botdagi /report buyrug'i bilan bir xil natijani beradi:
 * ikkita varaqni (Savdo, Qoldiq) PDF qilib, Telegram guruh/kanaliga yuboradi.
 * Python bot bilan hech qanday bog'lanish yo'q — bu mustaqil skript, chunki
 * Apps Script serverga to'g'ridan-to'g'ri ulana olmaydi (bu uchun serverda
 * ochiq HTTPS endpoint kerak bo'lardi). Shuning uchun eksport parametrlari
 * va fayl nomlash Python tomonidagi app/sheets_service.py va
 * app/pdf_service.py bilan qo'lda mos qilib qo'yilgan — ikkalasi ham bir xil
 * natija beradi.
 *
 * O'RNATISH:
 * 1. Extensions > Apps Script > Project Settings (⚙️) > Script Properties
 *    bo'limiga quyidagilarni qo'shing (.env fayldagi qiymatlar bilan bir xil):
 *      TELEGRAM_BOT_TOKEN
 *      TELEGRAM_CHAT_ID     (TELEGRAM_CHANNEL_ID qiymati, masalan -1001234567890)
 *      WORKSHEET_1_ID       (Savdo varag'ining sheetId/gid raqami)
 *      WORKSHEET_2_ID       (Qoldiq varag'ining sheetId/gid raqami)
 * 2. sendReportToTelegram funksiyasini bir marta "Run" qilib, ruxsat bering.
 * 3. Insert > Drawing orqali tugma yasab, unga "Assign script" orqali
 *    sendReportToTelegram funksiyasini biriktiring.
 *
 * To'liq qadamlar README.md faylida.
 */

function sendReportToTelegram() {
  const ui = SpreadsheetApp.getUi();
  const lock = LockService.getScriptLock();

  if (!lock.tryLock(5000)) {
    ui.alert("⚠️ Hisobot allaqachon yuborilyapti. Birozdan so'ng qayta urinib ko'ring.");
    return;
  }

  try {
    const props = PropertiesService.getScriptProperties();
    const botToken = props.getProperty('TELEGRAM_BOT_TOKEN');
    const chatId = props.getProperty('TELEGRAM_CHAT_ID');
    const worksheet1Id = props.getProperty('WORKSHEET_1_ID');
    const worksheet2Id = props.getProperty('WORKSHEET_2_ID');

    if (!botToken || !chatId || !worksheet1Id || !worksheet2Id) {
      ui.alert(
        "❌ Sozlamalar to'liq emas.\n" +
        'Project Settings > Script Properties bo\'limida ' +
        'TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WORKSHEET_1_ID, WORKSHEET_2_ID ' +
        "borligini tekshiring."
      );
      return;
    }

    const spreadsheetId = SpreadsheetApp.getActiveSpreadsheet().getId();
    const dateStr = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');

    const worksheets = [
      { sheetId: worksheet1Id, slug: 'savdo' },
      { sheetId: worksheet2Id, slug: 'qoldiq' },
    ];

    const results = [];
    for (const ws of worksheets) {
      try {
        const pdfBlob = exportSheetToPdf_(spreadsheetId, ws.sheetId, ws.slug, dateStr);
        sendDocumentToTelegram_(botToken, chatId, pdfBlob);
        results.push('✅ ' + ws.slug + ": muvaffaqiyatli yuborildi");
      } catch (err) {
        results.push('❌ ' + ws.slug + ': ' + err.message);
      }
    }

    ui.alert(results.join('\n'));
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
