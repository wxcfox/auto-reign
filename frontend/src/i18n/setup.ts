import i18next from "i18next";
import { initReactI18next } from "react-i18next";

import adminEn from "@/i18n/locales/en/admin.json";
import agentsEn from "@/i18n/locales/en/agents.json";
import chatEn from "@/i18n/locales/en/chat.json";
import commonEn from "@/i18n/locales/en/common.json";
import knowledgeEn from "@/i18n/locales/en/knowledge.json";
import workspacesEn from "@/i18n/locales/en/workspaces.json";
import adminZh from "@/i18n/locales/zh-CN/admin.json";
import agentsZh from "@/i18n/locales/zh-CN/agents.json";
import chatZh from "@/i18n/locales/zh-CN/chat.json";
import commonZh from "@/i18n/locales/zh-CN/common.json";
import knowledgeZh from "@/i18n/locales/zh-CN/knowledge.json";
import workspacesZh from "@/i18n/locales/zh-CN/workspaces.json";

export const supportedLanguages = ["en", "zh-CN"] as const;
export const namespaces = [
  "admin",
  "agents",
  "chat",
  "common",
  "knowledge",
  "workspaces",
] as const;

const resources = {
  en: {
    admin: adminEn,
    agents: agentsEn,
    chat: chatEn,
    common: commonEn,
    knowledge: knowledgeEn,
    workspaces: workspacesEn,
  },
  "zh-CN": {
    admin: adminZh,
    agents: agentsZh,
    chat: chatZh,
    common: commonZh,
    knowledge: knowledgeZh,
    workspaces: workspacesZh,
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
