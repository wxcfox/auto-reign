import i18next from "i18next";
import { initReactI18next } from "react-i18next";

import commonEn from "@/i18n/locales/en/common.json";
import dashboardEn from "@/i18n/locales/en/dashboard.json";
import interviewEn from "@/i18n/locales/en/interview.json";
import libraryEn from "@/i18n/locales/en/library.json";
import reviewEn from "@/i18n/locales/en/review.json";
import commonZh from "@/i18n/locales/zh-CN/common.json";
import dashboardZh from "@/i18n/locales/zh-CN/dashboard.json";
import interviewZh from "@/i18n/locales/zh-CN/interview.json";
import libraryZh from "@/i18n/locales/zh-CN/library.json";
import reviewZh from "@/i18n/locales/zh-CN/review.json";

export const supportedLanguages = ["en", "zh-CN"] as const;
export const namespaces = ["common", "dashboard", "library", "interview", "review"] as const;

const resources = {
  en: {
    common: commonEn,
    dashboard: dashboardEn,
    interview: interviewEn,
    library: libraryEn,
    review: reviewEn,
  },
  "zh-CN": {
    common: commonZh,
    dashboard: dashboardZh,
    interview: interviewZh,
    library: libraryZh,
    review: reviewZh,
  },
};

export async function initI18n() {
  if (i18next.isInitialized) {
    return i18next;
  }

  await i18next.use(initReactI18next).init({
    lng: "en",
    fallbackLng: "en",
    resources,
    defaultNS: "common",
    ns: [...namespaces],
    interpolation: {
      escapeValue: false,
    },
    debug: process.env.NODE_ENV === "development",
  });

  return i18next;
}

export default i18next;
