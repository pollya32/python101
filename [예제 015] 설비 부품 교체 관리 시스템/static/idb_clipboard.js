// 유닛/부품 복사-붙여넣기 클립보드 저장소 (IndexedDB 기반).
// localStorage(브라우저당 5~10MB)와 달리 용량 제한이 훨씬 커서, 도면 이미지가 포함된
// 유닛/부품을 여러 개 복사해도 용량 초과로 실패하지 않는다.
const IDB_CLIPBOARD_DB = "equipmentAppClipboard";
const IDB_CLIPBOARD_STORE = "clipboard";

function idbClipboardOpen() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IDB_CLIPBOARD_DB, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore(IDB_CLIPBOARD_STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function idbClipboardSet(key, value) {
  const db = await idbClipboardOpen();
  try {
    await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_CLIPBOARD_STORE, "readwrite");
      tx.objectStore(IDB_CLIPBOARD_STORE).put(value, key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

async function idbClipboardGet(key) {
  const db = await idbClipboardOpen();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_CLIPBOARD_STORE, "readonly");
      const req = tx.objectStore(IDB_CLIPBOARD_STORE).get(key);
      req.onsuccess = () => resolve(req.result ?? null);
      req.onerror = () => reject(req.error);
    });
  } finally {
    db.close();
  }
}
