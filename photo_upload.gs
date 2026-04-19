// ============================================================
// Samurai System - フォトアップロード GAS スクリプト
// ============================================================
// デプロイ設定:
//   種類: ウェブアプリ
//   実行するユーザー: 自分（Me）
//   アクセス: 全員（Anyone）
// ============================================================

// Google Drive フォルダID（各フォルダの URL 末尾の文字列）
// 例: https://drive.google.com/drive/folders/XXXX → XXXX
const FOLDER_IDS = {
  eyebrow: '1dSmKijEYpT1nOixWd9h2LZeG5sCa0VxR', // 眉毛フォルダID
  nail:    '1tPNH-1YHBDdqPH1pZILT_N73t0P7fxp2', // メンズネイルフォルダID
  other:   '1t-PQz3mVDfBfm5CqxSxSk2KFBuVHKaHg', // その他フォルダID
};

function doPost(e) {
  try {
    // Content-Type: text/plain でポストされるため postData.contents からパース
    const body = JSON.parse(e.postData.contents);
    const { fileName, mimeType, fileData, folder } = body;

    if (!fileData) {
      return jsonResponse({ success: false, error: 'fileData が空です' });
    }
    if (!FOLDER_IDS[folder]) {
      return jsonResponse({ success: false, error: '不明なフォルダ: ' + folder });
    }

    // base64 → Blob に変換
    const decoded = Utilities.base64Decode(fileData);
    const blob    = Utilities.newBlob(decoded, mimeType, fileName);

    // Google Drive にアップロード
    const driveFolder = DriveApp.getFolderById(FOLDER_IDS[folder]);
    const uploadedFile = driveFolder.createFile(blob);

    // ファイルをリンクで共有（誰でも閲覧可）
    uploadedFile.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);

    const fileId  = uploadedFile.getId();
    const thumbUrl = 'https://drive.google.com/thumbnail?id=' + fileId + '&sz=w400';

    return jsonResponse({
      success:  true,
      fileId:   fileId,
      fileName: uploadedFile.getName(),
      thumbUrl: thumbUrl,
    });

  } catch (err) {
    return jsonResponse({ success: false, error: err.toString() });
  }
}

// GET リクエストは動作確認用
function doGet(e) {
  return ContentService
    .createTextOutput(JSON.stringify({ status: 'ok', message: 'Photo upload GAS is running' }))
    .setMimeType(ContentService.MimeType.JSON);
}

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

// ============================================================
// フォルダID確認用（手動実行）
// GASのログでフォルダ名を確認できます
// ============================================================
function checkFolders() {
  Object.entries(FOLDER_IDS).forEach(([key, id]) => {
    try {
      const folder = DriveApp.getFolderById(id);
      Logger.log(`[${key}] OK: ${folder.getName()} (${id})`);
    } catch (e) {
      Logger.log(`[${key}] ❌ フォルダが見つかりません: ${id}`);
    }
  });
}
