/**
 * Записанные ответы плана работ для тестов раздела: вкладка «Гипотезы» спрашивает планы своим
 * запросом (`planApi.useLivePlans`), и тест вкладки без плана записывает пустой список.
 */

import type { Answer } from '../test/server';

export const NO_PLANS: Record<string, Answer> = { 'GET /api/sales/plans': { body: { plans: [] } } };
