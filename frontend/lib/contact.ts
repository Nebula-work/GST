// Shared support / ownership details used in the footer and privacy page.

export const OWNER_NAME = "Niladri Adak";
export const SUPPORT_EMAIL = "niladri.adak23@gmail.com";

export const SUPPORT_HREF =
  `mailto:${SUPPORT_EMAIL}` +
  `?subject=${encodeURIComponent("GST Reconciliation — Support request")}` +
  `&body=${encodeURIComponent(
    "Hi Niladri,\n\nI'm using the GST Purchase Matcher and need help with:\n\n" +
      "(Please describe the issue. If it's about a specific file, mention which " +
      "column or invoice looked wrong.)\n",
  )}`;
