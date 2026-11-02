/**
 * Верх «Обзора»: карточка раздела и плитки «Ждут человека». Плитки — по правам этапов: без права
 * этапа их нет вовсе, и сетка на три колонки, а не с дырами (`OverviewPage`).
 */

import { Anchor, Card, Group, SimpleGrid, Title } from '@mantine/core';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import type { OverviewWaiting } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { formatNumber } from '../format';

/** Карточка раздела сводки: заголовок, ссылка на экран и содержимое. */
export function Section({
  title,
  to,
  toTitle,
  aside,
  children,
}: {
  title: string;
  to?: string;
  toTitle?: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card className="glass" p="lg">
      {/* Ссылка раздела всегда в первой строке справа: на телефоне заголовок
          со значком переносится сам, а «К письмам» уезжала под заголовок
          и читалась подписью значка (аудит 25.09.2026, №28). */}
      <Group justify="space-between" align="flex-start" mb="md" gap="xs" wrap="nowrap">
        <Group gap="sm" align="center" style={{ minWidth: 0 }}>
          <Title order={5}>{title}</Title>
          {aside}
        </Group>
        {/* Средний вес, а не обычный: тонкие бирюзовые штрихи на ярком месте
            стекла читались бледнее цвета, которым написаны, — замер дал
            4,03 : 1 при ядре буквы 6,3 : 1 (25.09.2026). */}
        {to !== undefined && (
          <Anchor
            component={Link}
            to={to}
            size="sm"
            fw={500}
            style={{
              whiteSpace: 'nowrap',
              flexShrink: 0,
              lineHeight: 'var(--mantine-h5-line-height)',
            }}
          >
            {toTitle}
          </Anchor>
        )}
      </Group>
      {children}
    </Card>
  );
}

/** «в очереди №18», «в очередях №24, №21 и №18», «… и ещё 2». */
function runsHint(runs: number[]): string {
  const named = runs.slice(0, 3).map((id) => `№${id}`);
  if (runs.length === 1) return `в очереди ${named[0]}`;
  if (runs.length <= 3) return `в очередях ${named.slice(0, -1).join(', ')} и ${named.at(-1)}`;
  return `в очередях ${named.join(', ')} и ещё ${runs.length - 3}`;
}

/** Работа есть — число янтарное: мята здесь не нужна, «ноль» и так тихий. */
export function attention(count: number): string | undefined {
  return count > 0 ? 'yellow' : undefined;
}

/** Формы ждут у доноров и рекламодателей (сверка ТЗ 11.10.2026): рекламодатели названы,
 *  чтобы «и N с формой» в воронке доноров сходилось с донорской частью числа. */
function formsHint({ forms, advertiser_forms: advertisers = 0 }: OverviewWaiting): string {
  if (forms === 0) return 'форм в очереди нет';
  if (advertisers === 0) return 'у сайта форма вместо адреса';
  return `доноров ${formatNumber(forms - advertisers)} · рекламодателей ${formatNumber(advertisers)}`;
}

/** Части числа меню: спорные и бизнесы ниши ждут «пишем / не пишем» (проверка прода 10.10.2026). */
function advertisersHint({ advertisers, niche }: OverviewWaiting): string {
  const parts = [
    advertisers > niche ? `спорных ${formatNumber(advertisers - niche)}` : '',
    niche > 0 ? `бизнесов ниши ${formatNumber(niche)}` : '',
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : 'решений не ждут';
}

export function WaitingSection({ waiting }: { waiting: OverviewWaiting }) {
  const { can } = useSession();
  const newest = waiting.review_runs[0];
  // Без права этапа его плиток нет: сервер считает их нулями, а не «ждут ноль».
  const stageOne = can('donors');
  const stageTwo = can('advertisers');
  return (
    <Section title="Ждут человека">
      <SimpleGrid
        cols={{ base: 2, sm: 3, lg: stageOne && stageTwo ? 5 : 3 }}
        spacing="sm"
        className="metricGrid"
      >
        {stageOne && (
          <>
            <Metric
              title="Рассмотреть домены"
              value={formatNumber(waiting.review)}
              hint={waiting.review > 0 ? runsHint(waiting.review_runs) : 'очереди разобраны'}
              color={attention(waiting.review)}
              to={newest !== undefined ? `/runs/${newest}/review` : '/run'}
            />
            <Metric
              title="Разобрать цены"
              value={formatNumber(waiting.prices)}
              hint={waiting.prices > 0 ? 'цену подтверждает человек' : 'все ответы разобраны'}
              color={attention(waiting.prices)}
              // В тот же фильтр, которым плитка считается: весь список диалогов
              // прятал нужные среди сотни остальных (аудит 25.09.2026, №25). Цену
              // разбирают только у доноров — и плитка ведёт в «Диалоги с донорами».
              to="/threads?stage=donors&state=needs_review"
            />
          </>
        )}
        <Metric
          title="Заполнить формы"
          value={formatNumber(waiting.forms)}
          hint={formsHint(waiting)}
          color={attention(waiting.forms)}
          to="/forms"
        />
        {stageTwo && (
          <>
            <Metric
              title="Лиды рекламодателей"
              value={formatNumber(waiting.leads)}
              hint={waiting.leads > 0 ? 'ответили на оффер' : 'новых лидов нет'}
              color={attention(waiting.leads)}
              to="/threads?stage=advertisers&state=lead"
            />
            <Metric
              title="Кандидаты в рекламодатели"
              value={formatNumber(waiting.advertisers)}
              hint={advertisersHint(waiting)}
              color={attention(waiting.advertisers)}
              to="/advertisers?view=review"
            />
          </>
        )}
      </SimpleGrid>
    </Section>
  );
}
