function jsonResponse(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

function doPost(event) {
  try {
    const payload = JSON.parse(event.postData.contents || "{}");
    const properties = PropertiesService.getScriptProperties();
    const expectedSecret = properties.getProperty("MAIL_WEBHOOK_SECRET");
    const allowedRecipient = properties.getProperty("MAIL_ALLOWED_RECIPIENT");

    if (!expectedSecret || payload.secret !== expectedSecret) {
      return jsonResponse({ ok: false, error: "unauthorized" });
    }
    if (!allowedRecipient || payload.to !== allowedRecipient) {
      return jsonResponse({ ok: false, error: "recipient_not_allowed" });
    }
    if (!payload.subject || !payload.body) {
      return jsonResponse({ ok: false, error: "missing_content" });
    }

    GmailApp.sendEmail(payload.to, payload.subject, payload.body, {
      name: payload.senderName || "Moneymoney AI 機器人",
    });
    return jsonResponse({ ok: true });
  } catch (error) {
    return jsonResponse({ ok: false, error: String(error).slice(0, 180) });
  }
}
