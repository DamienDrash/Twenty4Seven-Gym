from nuki_integration.db import Database
from nuki_integration.config import Settings
import json

header = """<style type="text/css">
  body, table, td, p, a, li { -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%; }
  table, td { mso-table-lspace:0pt; mso-table-rspace:0pt; border-collapse:collapse; }
  img { -ms-interpolation-mode:bicubic; border:0; display:block; outline:none; }
  * { box-sizing:border-box; }
  :root { color-scheme:light only; supported-color-schemes:light; }
  body { margin:0!important; padding:0!important; background-color:#f0ede9!important; color:#000000!important; width:100%!important; }
  [data-ogsc] .fbb { background-color:#f0ede9!important; }
  [data-ogsc] .fbw { background-color:#ffffff!important; }
  [data-ogsc] .fbk { background-color:#000000!important; }
  [data-ogsc] .fcw { color:#ffffff!important; }
  [data-ogsc] .fcb { color:#000000!important; }
  [data-ogsc] .fcd { color:#3a3a3a!important; }
  [data-ogsc] .fcm { color:#7a7a7a!important; }
  [data-ogsc] .fbd { border-color:#e4e0db!important; }
  [data-ogsc] .fbtn { background-color:#b5ac9e!important; }
  @media (prefers-color-scheme:dark) {
    body { background-color:#f0ede9!important; }
    .fbb { background-color:#f0ede9!important; }
    .fbw { background-color:#ffffff!important; }
    .fbk { background-color:#000000!important; }
    .fcw { color:#ffffff!important; }
    .fcb { color:#000000!important; }
    .fcd { color:#3a3a3a!important; }
    .fcm { color:#7a7a7a!important; }
    .fbd { border-color:#e4e0db!important; }
    .fbtn { background-color:#b5ac9e!important; }
  }
  @media only screen and (max-width:620px) {
    .wrapper { width:100%!important; max-width:100%!important; }
    .h1 { font-size:27px!important; line-height:1.25!important; }
    .ph { padding-left:24px!important; padding-right:24px!important; }
    .p-hero { padding:36px 24px 28px!important; }
    .p-sec { padding:28px 24px!important; }
    .p-cta { padding:4px 24px 40px!important; }
  }
</style>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f0ede9;" class="fbb">
<tr><td align="center" style="padding:28px 16px;">
<table role="presentation" class="wrapper" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:600px;">
<tr>
  <td class="fbk" style="background-color:#000000;padding:22px 40px;text-align:center;">
    <a href="https://getimpulse.de/" style="font-family:Arial,Helvetica,sans-serif;font-size:18px;font-weight:700;letter-spacing:4px;color:#ffffff;text-decoration:none;text-transform:uppercase;" class="fcw">GETIMPULSE</a>
  </td>
</tr>"""

body = """<tr>
  <td class="fbw p-hero" style="background-color:#ffffff;padding:52px 56px 36px;text-align:center;">
    <h1 class="h1 fcb" style="font-family:Arial,Helvetica,sans-serif;font-size:36px;font-weight:700;color:#000000;margin:0 0 22px 0;line-height:1.2;">Dein Zugangscode</h1>
    <p class="fcd" style="font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#3a3a3a;margin:0;line-height:1.7;">Hallo {member_name},<br><br>dein Training kann starten! Hier ist dein persönlicher Zugangscode für deinen gebuchten Slot.</p>
  </td>
</tr>
<tr>
  <td class="fbw ph" style="background-color:#ffffff;padding:0 56px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td class="fbd" style="border-top:1px solid #e4e0db;font-size:0;line-height:0;">&nbsp;</td></tr></table>
  </td>
</tr>
<tr>
  <td class="fbw p-sec" style="background-color:#ffffff;padding:36px 56px;text-align:center;">
    <h3 class="fcb" style="font-family:Arial,Helvetica,sans-serif;font-size:19px;font-weight:700;color:#000000;margin:0 0 12px 0;line-height:1.3;">Code: {code}</h3>
    <p class="fcd" style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#3a3a3a;margin:0 0 18px 0;line-height:1.7;">
      Gültig von: {valid_from}<br>
      Gültig bis: {valid_until}
    </p>
  </td>
</tr>
<tr>
  <td class="fbw p-cta" style="background-color:#ffffff;padding:4px 56px 52px;text-align:center;">
    {checks_row}
  </td>
</tr>"""

footer = """<tr>
  <td class="fbk" style="background-color:#000000;padding:40px 40px 32px;text-align:center;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" align="center" style="margin:0 auto 28px auto;">
      <tr>
        <td style="padding:0 10px;"><a href="https://www.instagram.com/getimpulse/" title="Instagram" style="display:inline-block;"><svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24"><rect x="2" y="2" width="20" height="20" rx="5" fill="none" stroke="#ffffff" stroke-width="1.8"/><circle cx="12" cy="12" r="4.2" fill="none" stroke="#ffffff" stroke-width="1.8"/><circle cx="17.3" cy="6.7" r="1.1" fill="#ffffff"/></svg></a></td>
        <td style="padding:0 10px;"><a href="https://www.youtube.com/@getimpulse886" title="YouTube" style="display:inline-block;"><svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24"><path d="M22.5 6.6s-.2-1.7-1-2.4c-.9-1-2-1-2.4-1.1C16.5 3 12 3 12 3s-4.5 0-7.1.1c-.5.1-1.5.1-2.4 1.1C1.7 4.9 1.5 6.6 1.5 6.6S1.2 8.5 1.2 10.4v1.8c0 1.9.3 3.8.3 3.8s.2 1.7 1 2.4c.9 1 2.1.9 2.7 1 1.9.2 8.3.2 8.3.2s4.5 0 7.1-.2c.5-.1 1.5-.1 2.4-1.1.8-.7 1-2.4 1-2.4s.3-1.9.3-3.8V10.4c0-1.9-.3-3.8-.3-3.8z" fill="#ffffff"/><polygon points="9.7,15.5 9.7,8.4 16.1,12" fill="#000000"/></svg></a></td>
        <td style="padding:0 10px;"><a href="https://www.tiktok.com/@getimpulse" title="TikTok" style="display:inline-block;"><svg xmlns="http://www.w3.org/2000/svg" width="24" height="26" viewBox="0 0 24 26"><path d="M19.3 5.1A4.6 4.6 0 0 1 14.8.5h-3.4v14.9a2.8 2.8 0 0 1-2.8 2.5 2.8 2.8 0 0 1-2.8-2.8 2.8 2.8 0 0 1 2.8-2.8c.3 0 .5 0 .8.1V9a6.2 6.2 0 0 0-.8-.1 6.2 6.2 0 0 0-6.2 6.2 6.2 6.2 0 0 0 6.2 6.2 6.2 6.2 0 0 0 6.2-6.2V7.5a7.9 7.9 0 0 0 4.6 1.5V5.6a4.6 4.6 0 0 1-3.1-1.5z" fill="#ffffff"/></svg></a></td>
        <td style="padding:0 10px;"><a href="https://www.facebook.com/getimpulse.berlinheidestrasse/" title="Facebook" style="display:inline-block;"><svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 24 24"><path d="M24 12.1C24 5.4 18.6 0 12 0S0 5.4 0 12.1c0 6 4.4 11 10.1 11.9V15.6H7.1v-3.5h3V9.4c0-3 1.8-4.6 4.5-4.6 1.3 0 2.6.2 2.6.2v2.9h-1.5c-1.4 0-1.9.9-1.9 1.8v2.2h3.2l-.5 3.5h-2.7V24C19.6 23.1 24 18.1 24 12.1z" fill="#ffffff"/></svg></a></td>
      </tr>
    </table>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:20px;">
      <tr><td style="border-top:1px solid #2c2c2c;font-size:0;line-height:0;">&nbsp;</td></tr>
    </table>
    <p class="fcw" style="font-family:Arial,Helvetica,sans-serif;font-size:13px;font-weight:700;color:#ffffff;margin:0 0 10px 0;line-height:1.5;letter-spacing:0.5px;">Get-Impulse Berlin GmbH</p>
    <p class="fcm" style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#7a7a7a;margin:0 0 4px 0;line-height:1.7;">Heidestraße 11, 10557 Berlin</p>
    <p class="fcm" style="font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#7a7a7a;margin:0 0 24px 0;line-height:1.7;"><a href="tel:+493030106609" style="color:#7a7a7a;text-decoration:none;">030 30106609</a></p>
    <p style="font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#4a4a4a;margin:0;line-height:1.7;">
      Du erhältst diese E-Mail aufgrund deiner Mitgliedschaft oder Buchung.<br>
      <a href="https://getimpulse.de/datenschutz" style="color:#7a7a7a;text-decoration:underline;">Datenschutz</a>&nbsp;&middot;&nbsp;<a href="https://getimpulse.de/impressum" style="color:#7a7a7a;text-decoration:underline;">Impressum</a>
    </p>
  </td>
</tr>
</table>
</td></tr>
</table>"""

settings = Settings()
db = Database(settings.database_url)
db.open()
tpl = {
    "header_html": header,
    "body_html": "<tr><td class='fbw' style='padding:40px; background:#fff;'>Standard Nachricht</td></tr>",
    "footer_html": footer,
    "access_code_body_html": body,
    "reset_body_html": body # simplified for reset
}
db.set_system_setting(key="email_template", value=tpl)
db.close()
print("SUCCESS: Template reset to user specification.")
