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

/**
 * Diagnostika: Script Properties'da aynan qanday kalitlar saqlanganini
 * ko'rsatadi. Apps Script muharririda ushbu funksiyani tanlab "Run" bosing,
 * so'ng chap paneldagi "Executions" (yoki View > Logs) bo'limini oching.
 * Kalit nomlari aniq TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WORKSHEET_1_ID,
 * WORKSHEET_2_ID bilan mos kelishi kerak (katta-kichik harf va bo'shliqlar
 * ham muhim — masalan "TELEGRAM_CHANNEL_ID" deb yozilgan bo'lsa,
 * bu "TELEGRAM_CHAT_ID" bilan bir xil emas va topilmaydi).
 */
function debugProperties() {
  const props = PropertiesService.getScriptProperties().getProperties();
  Logger.log('Script Properties ichidagi barcha kalitlar: ' + JSON.stringify(Object.keys(props)));

  ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'WORKSHEET_1_ID', 'WORKSHEET_2_ID', 'WORKSHEET_1_DATE_CELL', 'WORKSHEET_2_DATE_CELL'].forEach(function (key) {
    const value = props[key];
    if (!value && key.indexOf('_DATE_CELL') !== -1) {
      Logger.log(key + " -> bo'sh (ixtiyoriy: sana avtomatik topiladi)");
    } else if (!value) {
      Logger.log(key + " -> TOPILMADI (bo'sh yoki kalit nomi noto'g'ri)");
    } else if (key === 'TELEGRAM_BOT_TOKEN') {
      Logger.log(key + ' -> mavjud (uzunligi: ' + value.length + ' belgi)');
    } else {
      Logger.log(key + ' -> ' + value);
    }
  });
}

/**
 * Jadval ochilganda yuqoridagi menyu qatoriga (Help yonidan) "📄 Hisobot"
 * nomli maxsus menyu qo'shadi. DIQQAT: bu menyudagi elementni bosish ham
 * "sendReportToTelegram" ni chaqiradi — ya'ni faqat Edit huquqi bor va
 * shaxsan avtorizatsiyadan o'tgan odamlar uchun ishlaydi (Drawing bilan
 * bir xil cheklov). Viewer'lar uchun baribir Web App havolasi (Option A,
 * README) kerak bo'ladi.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('📄 Hisobot')
    .addItem('Hisobotni yuborish', 'sendReportToTelegram')
    .addToUi();
}

/** Editor jadval ichidan chizma (drawing) yoki menyu orqali ishga tushiradi. */
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
    // Ixtiyoriy: hisobot sanasi turgan katak (masalan "B2"). Bo'sh bo'lsa,
    // varaqning yuqori-chap A1:Z40 qismidan birinchi sana avtomatik topiladi.
    const worksheet1DateCell = props.getProperty('WORKSHEET_1_DATE_CELL');
    const worksheet2DateCell = props.getProperty('WORKSHEET_2_DATE_CELL');

    if (!botToken || !chatId || !worksheet1Id || !worksheet2Id) {
      return {
        ok: false,
        message:
          "Sozlamalar to'liq emas. Project Settings > Script Properties bo'limida " +
          "TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, WORKSHEET_1_ID, WORKSHEET_2_ID borligini tekshiring.",
      };
    }

    const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
    const spreadsheetId = spreadsheet.getId();

    const worksheets = [
      { sheetId: worksheet1Id, slug: 'savdo', dateCell: worksheet1DateCell },
      { sheetId: worksheet2Id, slug: 'qoldiq', dateCell: worksheet2DateCell },
    ];

    const lines = [];
    let allOk = true;
    for (const ws of worksheets) {
      try {
        // Fayl nomidagi sana yuborilgan kun emas, varaq ICHIDA yozilgan
        // hisobot sanasi bo'lishi kerak (1-sentyabr hisoboti 3-sentyabrda
        // yuborilsa ham "savdo_2026-09-01.pdf"). Sana topilmasa bugungi kun.
        const dateStr = resolveReportDateString_(spreadsheet, ws.sheetId, ws.dateCell);
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

/**
 * Varaq ichidagi hisobot sanasini 'yyyy-MM-dd' ko'rinishida qaytaradi.
 * dateCell berilgan bo'lsa faqat o'sha katak o'qiladi; aks holda A1:Z40
 * oralig'i qatorma-qator (chapdan o'ngga) skanerlanib, birinchi sana
 * ko'rinishidagi katak olinadi. Python tomonidagi app/sheet_date.py bilan
 * bir xil qoidalar. Sana topilmasa bugungi kun qaytariladi.
 */
function resolveReportDateString_(spreadsheet, sheetId, dateCell) {
  const tz = spreadsheet.getSpreadsheetTimeZone();
  const today = new Date();
  const defaultYear = Number(Utilities.formatDate(today, tz, 'yyyy'));

  const sheet = spreadsheet.getSheets().find(function (s) {
    return String(s.getSheetId()) === String(sheetId);
  });
  if (!sheet) {
    return Utilities.formatDate(today, tz, 'yyyy-MM-dd');
  }

  let found = null;
  if (dateCell && String(dateCell).trim()) {
    found = dateFromCellValue_(sheet.getRange(String(dateCell).trim()).getValue(), defaultYear);
  } else {
    const rows = Math.min(40, Math.max(sheet.getLastRow(), 1));
    const cols = Math.min(26, Math.max(sheet.getLastColumn(), 1));
    const values = sheet.getRange(1, 1, rows, cols).getValues();
    outer: for (let r = 0; r < values.length; r++) {
      for (let c = 0; c < values[r].length; c++) {
        found = dateFromCellValue_(values[r][c], defaultYear);
        if (found) break outer;
      }
    }
  }

  if (!found) {
    Logger.log('Varaq ' + sheetId + ' ichida sana topilmadi, bugungi kun ishlatiladi');
    return Utilities.formatDate(today, tz, 'yyyy-MM-dd');
  }
  // Katakdan kelgan Date obyekti jadval vaqt mintaqasida; matndan yasalgani
  // esa "mahalliy" komponentlar bilan yaratilgan — ikkalasi ham shu yerda
  // sana komponentlaridan to'g'ridan-to'g'ri formatlanadi.
  return found.y + '-' + pad2_(found.m) + '-' + pad2_(found.d);
}

/** Katak qiymatini {y, m, d} ga aylantiradi; sana bo'lmasa null. */
function dateFromCellValue_(value, defaultYear) {
  if (value instanceof Date && !isNaN(value.getTime())) {
    const tz = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone();
    return {
      y: Number(Utilities.formatDate(value, tz, 'yyyy')),
      m: Number(Utilities.formatDate(value, tz, 'M')),
      d: Number(Utilities.formatDate(value, tz, 'd')),
    };
  }
  if (typeof value === 'string' && value.trim()) {
    return parseDateText_(value, defaultYear);
  }
  return null;
}

const MONTH_PATTERNS_ = [
  [1, 'yanvar|январ[ья]?|jan(?:uary)?'],
  [2, 'fevral|феврал[ья]?|feb(?:ruary)?'],
  [3, 'mart|март[а]?|mar(?:ch)?'],
  [4, 'aprel|апрел[ья]?|apr(?:il)?'],
  [5, 'may|ма[йя]'],
  [6, 'iyun|июн[ья]?|jun(?:e)?'],
  [7, 'iyul|июл[ья]?|jul(?:y)?'],
  [8, 'avgust|август[а]?|aug(?:ust)?'],
  [9, 'sentyabr|sentabr|сентябр[ья]?|sep(?:t(?:ember)?)?'],
  [10, 'oktyabr|oktabr|октябр[ья]?|oct(?:ober)?'],
  [11, 'noyabr|ноябр[ья]?|nov(?:ember)?'],
  [12, 'dekabr|декабр[ья]?|dec(?:ember)?'],
];
// JS'da \b kirill harflari bilan ishlamaydi, shuning uchun so'z chegarasi
// "harf emas" sifatida qo'lda tekshiriladi.
const LETTER_ = '[A-Za-zЀ-ӿʻ‘’\']';
const MONTH_ALT_ = MONTH_PATTERNS_.map(function (p) { return '(' + p[1] + ')'; }).join('|');

const NUMERIC_YMD_ = /(?:^|\D)(\d{4})[./-](\d{1,2})[./-](\d{1,2})(?!\d)/;
const NUMERIC_DMY_ = /(?:^|\D)(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})(?!\d)/;
const DAY_MONTH_ = new RegExp(
  '(?:^|\\D)(\\d{1,2})\\s*[-–—]?\\s*(?:' + MONTH_ALT_ + ')(?!' + LETTER_ + ')\\.?(?:\\s*[-,]?\\s*(\\d{4}))?',
  'i'
);
const MONTH_DAY_ = new RegExp(
  '(?:^|[^A-Za-zЀ-ӿ])(?:' + MONTH_ALT_ + ')(?!' + LETTER_ + ')\\.?\\s*(\\d{1,2})(?!\\d)(?:\\s*[-,]?\\s*(\\d{4}))?',
  'i'
);

/** "Sana: 01.09.2026", "1-sentyabr", "2 сентября 2026" kabi matndan sanani topadi. */
function parseDateText_(text, defaultYear) {
  const s = String(text).trim();
  let m;

  m = NUMERIC_YMD_.exec(s);
  if (m) {
    const r = safeDate_(Number(m[1]), Number(m[2]), Number(m[3]));
    if (r) return r;
  }
  m = NUMERIC_DMY_.exec(s);
  if (m) {
    const r = safeDate_(Number(m[3]), Number(m[2]), Number(m[1]));
    if (r) return r;
  }

  m = DAY_MONTH_.exec(s);
  if (m) {
    const month = monthFromGroups_(m, 2);
    const year = m[2 + MONTH_PATTERNS_.length] ? Number(m[2 + MONTH_PATTERNS_.length]) : defaultYear;
    const r = month && safeDate_(year, month, Number(m[1]));
    if (r) return r;
  }
  m = MONTH_DAY_.exec(s);
  if (m) {
    const month = monthFromGroups_(m, 1);
    const day = Number(m[1 + MONTH_PATTERNS_.length]);
    const year = m[2 + MONTH_PATTERNS_.length] ? Number(m[2 + MONTH_PATTERNS_.length]) : defaultYear;
    const r = month && safeDate_(year, month, day);
    if (r) return r;
  }
  return null;
}

function monthFromGroups_(match, offset) {
  for (let i = 0; i < MONTH_PATTERNS_.length; i++) {
    if (match[offset + i]) return MONTH_PATTERNS_[i][0];
  }
  return null;
}

function safeDate_(y, m, d) {
  if (y < 100) y += 2000;
  if (m < 1 || m > 12 || d < 1 || d > 31) return null;
  const probe = new Date(Date.UTC(y, m - 1, d));
  if (probe.getUTCFullYear() !== y || probe.getUTCMonth() !== m - 1 || probe.getUTCDate() !== d) {
    return null; // masalan 31.02
  }
  return { y: y, m: m, d: d };
}

function pad2_(n) {
  return (n < 10 ? '0' : '') + n;
}

/**
 * Sinov: Apps Script muharririda ushbu funksiyani "Run" qilib, Executions /
 * Logs bo'limida har bir varaq uchun qaysi sana topilganini ko'ring.
 * Telegramga hech narsa yuborilmaydi.
 */
function debugReportDates() {
  const props = PropertiesService.getScriptProperties();
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  [
    ['savdo', props.getProperty('WORKSHEET_1_ID'), props.getProperty('WORKSHEET_1_DATE_CELL')],
    ['qoldiq', props.getProperty('WORKSHEET_2_ID'), props.getProperty('WORKSHEET_2_DATE_CELL')],
  ].forEach(function (row) {
    const dateStr = resolveReportDateString_(spreadsheet, row[1], row[2]);
    Logger.log(row[0] + ' (sheetId=' + row[1] + ', katak=' + (row[2] || 'avto') + ') -> ' + row[0] + '_' + dateStr + '.pdf');
  });
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
