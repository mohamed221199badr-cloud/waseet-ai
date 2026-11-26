// Waseet: append each confirmed order to this Sheet. Paste in Extensions > Apps Script.
// Set the script property TOKEN (Project Settings > Script properties) to the shared secret.
const HEADERS = ['التاريخ', 'رقم الطلب', 'العميل', 'التليفون', 'الطريقة', 'العنوان',
                 'الأصناف', 'الإجمالي', 'أكده'];

function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const body = JSON.parse(e.postData.contents);
    if (body.token !== PropertiesService.getScriptProperties().getProperty('TOKEN')) {
      return out({ok: false, error: 'auth'});
    }
    const r = body.row;
    const sh = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
    if (sh.getLastRow() === 0) sh.appendRow(HEADERS);
    const ids = sh.getLastRow() > 1
      ? sh.getRange(2, 2, sh.getLastRow() - 1, 1).getValues().flat() : [];
    if (ids.indexOf(r.order_id) === -1) {       // retry-safe: one row per order
      sh.appendRow([r.time, r.order_id, r.customer, r.phone, r.method, r.address,
                    r.items, r.total, r.by]);
    }
    return out({ok: true});
  } finally {
    lock.releaseLock();
  }
}

function out(o) {
  return ContentService.createTextOutput(JSON.stringify(o))
    .setMimeType(ContentService.MimeType.JSON);
}
