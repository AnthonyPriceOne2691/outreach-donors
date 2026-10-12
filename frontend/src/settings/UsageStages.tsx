/**
 * Расход по этапам и свой потолок Этапа 2 на экране «Расход».
 *
 * Расход Этапа 2 на обход и прокси считается отдельно от Этапа 1 и держится своим
 * потолком: иначе один донор за защитой съест месяц. До 12.10.2026 у строки расхода
 * этапа не было, и DR кандидатов Этапа 2 стоял на экране одной строкой с метриками
 * прогона Этапа 1.
 *
 * **Единицы этапа не складываются в одно число.** Юниты, токены, письма и байты — разные
 * валюты, и сумма вышла бы числом, которого нет ни в одном счёте; поэтому у этапа —
 * перечисление по провайдерам и деньги отдельно.
 *
 * **Платный обход — своей строкой против своего потолка.** Потрачено — тем же числом,
 * с которым потолок сравнивает сам обход; потолка нет — так и сказано, а не «0 из 0»:
 * без него прокси и рендеринг не зовутся вовсе (`crawl/spend.py`).
 */

import { Card, Group, Stack, Text } from '@mantine/core';

import type { CrawlSpend, StageSpending } from '../api/settings';
import { Meter } from '../components/Meter';
import { formatUsd } from '../format';
import { spendProvider, stageTitle, unitsText } from './usageLabels';

/** Платный обход Этапа 2 с начала месяца — против своего потолка, если он задан. */
export function CrawlCap({ crawl }: { crawl: CrawlSpend }) {
  const spent = Number(crawl.spent_usd);
  if (crawl.cap_usd === null) {
    return (
      <Group justify="space-between">
        <Text size="sm">
          Этап 2 — прокси и рендеринг с начала месяца: <b>{formatUsd(spent)}</b>
        </Text>
        <Text size="sm" c="dimmed">
          своего потолка у Этапа 2 нет — прокси и рендеринг не зовутся, пока его не зададут
        </Text>
      </Group>
    );
  }
  const cap = Number(crawl.cap_usd);
  return (
    <Stack gap={6}>
      <Group justify="space-between">
        <Text size="sm">
          Этап 2 — прокси и рендеринг с начала месяца: <b>{formatUsd(spent)}</b> из {formatUsd(cap)}{' '}
          по своему потолку
        </Text>
        <Text size="sm" c="dimmed">
          {spent >= cap
            ? 'потолок выбран — платные уровни обхода не зовутся до следующего месяца'
            : 'отдельно от юнитов Ahrefs'}
        </Text>
      </Group>
      <Meter spent={spent} cap={cap} label="Прокси и рендеринг Этапа 2 с начала месяца" />
    </Stack>
  );
}

/** Части расхода этапа: провайдеры с тратами — каждый в своей валюте, — и деньги. */
function partsOf(stage: StageSpending): string[] {
  const parts = Object.entries(stage.units_by_provider)
    .filter(([, units]) => units > 0)
    .map(([provider, units]) => `${spendProvider(provider).title}: ${unitsText(provider, units)}`);
  const amount = Number(stage.amount_usd);
  if (amount > 0) parts.push(formatUsd(amount));
  return parts;
}

/** Перенесённые на телефоне части прижаты к своему этапу, а не к следующему: зазор
 *  между строками этапа меньше, чем между этапами (`Stack`). */
const ROW_GAP = { rowGap: 2 } as const;

/** Разбивка по этапам. Строка без этапа — последней: отправка письма общая на все этапы,
 *  а строки до 12.10.2026 и операции, которые делят этапы, этапа не несут.
 *
 *  Поля — те же 32 px, что у панели сверху и у таблицы (поле карточки плюс поле ячейки):
 *  текст соседних карточек начинается с одного места. На телефоне части этапа уходят под
 *  его название и стоят влево — прижатые вправо переносы читались лесенкой (снимок 390 px). */
export function StageBreakdown({ stages }: { stages: StageSpending[] }) {
  if (stages.length === 0) return null;
  return (
    <Card className="glass" px="xl" py="lg">
      <Stack gap="xs">
        <Text fw={600}>По этапам</Text>
        {stages.map((stage) => (
          <Group
            key={stage.stage ?? 'none'}
            justify="space-between"
            align="flex-start"
            gap="md"
            style={ROW_GAP}
          >
            <Text size="sm" fw={500}>
              {stageTitle(stage.stage)}
            </Text>
            <Text size="sm" c="dimmed" ta={{ base: 'left', sm: 'right' }}>
              {partsOf(stage).join(' · ') || 'трат не было'}
            </Text>
          </Group>
        ))}
      </Stack>
    </Card>
  );
}
