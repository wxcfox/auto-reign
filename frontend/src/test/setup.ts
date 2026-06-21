import "@testing-library/jest-dom/vitest";
import { beforeAll } from "vitest";

import { initI18n } from "@/i18n/setup";

beforeAll(async () => {
  await initI18n();
});
