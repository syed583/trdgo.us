/**
 * Trdgo Stock -> Google Sheet signal log.
 *
 * Setup
 * -----
 * 1. Create a new Google Sheet.
 * 2. Extensions -> Apps Script. Delete the sample code, paste this whole file,
 *    and Save.
 * 3. Deploy -> New deployment. Select type: "Web app".
 *       Description: Trdgo signal log
 *       Execute as:  Me
 *       Who has access: Anyone
 *    Deploy, then authorize when Google asks.
 * 4. Copy the "Web app URL" (it ends in /exec).
 * 5. Put it in backend/.env (locally AND on the VPS):
 *       GOOGLE_SHEET_WEBHOOK_URL=https://script.google.com/macros/s/..../exec
 *    then restart the backend.
 *
 * After that, every signal change on Trdgo Stock -- a new BUY or SELL, or a
 * BUY/SELL that goes NO TRADE -- appends a row with the price it happened at.
 */
// The spreadsheet to write to, by its ID (the long part of its /d/<ID>/edit URL).
// Using openById instead of getActiveSpreadsheet guarantees rows land in THIS
// sheet even if the script is not container-bound to it.
var SHEET_ID = '1xeAB13U4PwVOUy6gqUAL8vxTL8w6i6JPBw35HQC47Fw';

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var d = JSON.parse(e.postData.contents);
    var ss = SpreadsheetApp.openById(SHEET_ID);
    var sheet = ss.getSheetByName('Signals') || ss.insertSheet('Signals');
    if (sheet.getLastRow() === 0) {
      sheet.appendRow(['Time', 'Symbol', 'Horizon', 'Event', 'From', 'To',
        'Price', 'Decision', 'Buy score', 'Sell score', 'Target', 'Stop']);
      sheet.getRange('1:1').setFontWeight('bold');
      sheet.setFrozenRows(1);
    }
    sheet.appendRow([d.time, d.symbol, d.horizon, d.event, d.from, d.to,
      d.price, d.decision, d.buy_score, d.sell_score, d.target, d.stop]);
    return ContentService
      .createTextOutput(JSON.stringify({ ok: true }))
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return ContentService
      .createTextOutput(JSON.stringify({ ok: false, error: String(err) }))
      .setMimeType(ContentService.MimeType.JSON);
  } finally {
    lock.releaseLock();
  }
}

/** Optional: lets you open the web-app URL in a browser to confirm it is live. */
function doGet() {
  return ContentService
    .createTextOutput('Trdgo signal log is live. POST events to this URL.')
    .setMimeType(ContentService.MimeType.TEXT);
}
