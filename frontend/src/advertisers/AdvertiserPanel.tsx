/**
 * Панель раздела «Рекламодатели» — вид по умолчанию (тест-группа 31.10.2026): этапность выборки
 * сверху, качество загрузок и рассылка ниже — порядок вопросов: что вышло из доноров, какая загрузка
 * это дала и когда уйдут письма.
 */

import { Stack } from '@mantine/core';

import { PanelMailing } from './PanelMailing';
import { PanelQuality } from './PanelQuality';
import { PanelSteps } from './PanelSteps';

export function AdvertiserPanel() {
  return (
    <Stack gap="lg">
      <PanelSteps />
      <PanelQuality />
      <PanelMailing />
    </Stack>
  );
}
