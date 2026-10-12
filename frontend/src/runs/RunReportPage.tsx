/**
 * Отчёт прогона: сколько доменов проверено, сколько прошло фильтр, сколько отсеяно
 * и по какому порогу, сколько потрачено юнитов и денег — требование ТЗ к отчёту
 * прогона. Отчёт записывался, а экран прогона показывал из него только домены выдачи,
 * исключённые, смету и факт (сверка ТЗ 11.10.2026). Сюда ведёт номер прогона в строке
 * истории («№27») — ссылкой, как домен в таблице доноров ведёт в карточку.
 *
 * **Три вопроса — три места.** Шапка — что проверили и чем кончилось («подходит / не
 * подходит / не проверен» плитками). Ниже — отсев по каждому порогу вместе с порогами
 * этого прогона: пороги версионируются, и сегодняшние на экране «Пороги» могут быть
 * другими. Последнее — расход по журналу: Ahrefs юнитами, выдача деньгами, модель
 * токенами — в одну сумму они не складываются.
 *
 * **У идущего прогона чисел доменов нет, а не нули.** Отчёт пишется в конце попытки,
 * журнал расхода — на каждом чекпоинте: идущий прогон уже потратил, а итога у него ещё
 * нет. Ноль читался бы как «проверил и никого не нашёл».
 */

import { Alert, Badge, Card, Group, Loader, SimpleGrid, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { countryTitle, DONOR_STATUSES, RUN_STATUSES } from '../api/labels';
import { fetchRunReport } from '../api/runReport';
import type { RunDomains, RunReportView } from '../api/runReport';
import type { RunCard, RunStatus } from '../api/types';
import { Metric } from '../components/Metric';
import { formatDateTime, formatNumber, plural } from '../format';
import { RejectionsCard } from './RejectionsCard';
import { ReviewLink } from './ReviewLink';
import { ACTIVE } from './RunHistory';
import { missingRun, NoSuchRun, RunHead, useRunAddress } from './runAddress';
import type { RunAddress } from './runAddress';
import { SpendCard } from './SpendCard';

const HINT =
  'Числа доменов — из итога прогона: он записывается, когда прогон кончается. Расход — из журнала расхода: туда пишется каждый платный запрос, поэтому у идущего прогона расход уже есть, а итога ещё нет. Пороги — те, с которыми прогон запущен.';

const domainsWord = (count: number) => plural(count, 'домен', 'домена', 'доменов');

/** Слово вердикта с заглавной — подписью плитки: слова те же, что у доноров. */
const titled = (word: string) => word.charAt(0).toUpperCase() + word.slice(1);

/** Путь домена до вердикта одной строкой: из выдачи — мимо гейта и свежих — в Ahrefs. */
function Funnel({ domains }: { domains: RunDomains }) {
  const found =
    domains.found === null
      ? 'Из выдачи'
      : `Из выдачи — ${formatNumber(domains.found)} ${domainsWord(domains.found)}`;
  return (
    <Text size="sm">
      {found}: исключено {formatNumber(domains.excluded)}, проверены раньше{' '}
      {formatNumber(domains.fresh)} — метрики в сроке, платить не пришлось; новых в проверку Ahrefs
      — {formatNumber(domains.to_check)}.
    </Text>
  );
}

/** Новые, до Ahrefs не дошедшие. У законченного прогона их отрезал судья — до сбора
 *  метрик, когда он режет; у идущего их проверяет продолжение; у остановленного — не
 *  успели. Молчать нельзя: иначе «подходит + не подходит + не проверен» не сходится
 *  с «новых в проверку», и неясно почему. */
function NotReached({ domains, status }: { domains: RunDomains; status: RunStatus }) {
  if (domains.not_reached === 0) return null;
  const count = formatNumber(domains.not_reached);
  const of = `${count} из ${formatNumber(domains.to_check)}`;
  let said = `Не дошли до Ahrefs: ${of} — прогон остановился раньше.`;
  if (status === 'done' && domains.judge_enforced) {
    said = `Отрезал судья до Ahrefs: ${count} — за их метрики не платили.`;
  } else if (ACTIVE.has(status)) {
    said = `Прошлая попытка не дошла до Ahrefs у ${of} — их проверяет продолжение.`;
  }
  return (
    <Text size="sm" c="dimmed">
      {said}
    </Text>
  );
}

/** Факты прогона строкой: состояние, страна, ключи, когда запущен — и вход в рассмотрение. */
function Facts({ run, from }: { run: RunCard; from: string }) {
  const pending = run.queue.pending ?? 0;
  const queued = pending + (run.queue.accepted ?? 0) + (run.queue.rejected ?? 0);
  return (
    <Group gap="sm">
      <Badge variant="light" color={RUN_STATUSES[run.status].color}>
        {RUN_STATUSES[run.status].title}
      </Badge>
      <Text size="sm">
        {countryTitle(run.country)}, ключей {formatNumber(run.keywords)}, запущен{' '}
        {formatDateTime(run.started_at)}
        {/* Кто — по записи журнала о запуске; прогон из консоли её не оставляет. */}
        {run.started_by ? `, запустил ${run.started_by}` : ' — кто запустил, не записано'}
      </Text>
      {queued > 0 && (
        <ReviewLink run={run} from={from}>
          {pending > 0 ? `Рассмотреть ${formatNumber(pending)}` : 'Рассмотрение'}
        </ReviewLink>
      )}
    </Group>
  );
}

/** Домены прогона: путь, плитки вердиктов и что с остальными. У идущего прогона итог —
 *  прошлой попытки (продолжение после сбоя пишет свой в конце), и это сказано первым:
 *  иначе числа читались бы итогом, а «не дошли» — остановкой. */
function DomainsBlock({ run, domains }: { run: RunCard; domains: RunDomains }) {
  return (
    <Stack gap="sm">
      {ACTIVE.has(run.status) && (
        <Text size="sm" fw={500}>
          Идёт продолжение — числа прошлой попытки. Итог обновится, когда прогон закончится.
        </Text>
      )}
      <Funnel domains={domains} />
      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
        <Metric
          title="Проверено в Ahrefs"
          value={formatNumber(domains.checked)}
          hint="с любым вердиктом"
        />
        <Metric
          title={titled(DONOR_STATUSES.suitable.title)}
          value={formatNumber(domains.suitable)}
          hint="прошли фильтр"
        />
        <Metric
          title={titled(DONOR_STATUSES.unsuitable.title)}
          value={formatNumber(domains.unsuitable)}
          hint="отсеяны порогами"
        />
        {/* «Не проверен» — не отказ (ТЗ: «нет данных в Ahrefs — не проверен, в
            отчёт»): такой домен доберут следующим прогоном. */}
        <Metric
          title={titled(DONOR_STATUSES.unchecked.title)}
          value={formatNumber(domains.unchecked)}
          hint="Ahrefs не дал данных"
        />
      </SimpleGrid>
      <NotReached domains={domains} status={run.status} />
      {domains.resumes > 0 && (
        <Text size="sm" c="dimmed">
          Прогон продолжали после сбоя ({formatNumber(domains.resumes)}{' '}
          {plural(domains.resumes, 'раз', 'раза', 'раз')}): числа — последней попытки, а проверенное
          до сбоя — в «проверены раньше». Расход — всех попыток.
        </Text>
      )}
    </Stack>
  );
}

/** Шапка: возврат, заголовок, факты прогона и его домены плитками. Страница истории,
 *  откуда пришли, — и для «К прогонам», и для рассмотрения. */
function Head({ report, address }: { report: RunReportView; address: RunAddress }) {
  const { run, domains } = report;
  return (
    <Card className="glassPanel" p="xl">
      <Stack gap="md">
        <RunHead back={address.back} title={`Отчёт прогона №${run.id}`} hint={HINT}>
          <Facts run={run} from={address.from} />
        </RunHead>
        {domains === null ? (
          <Text size="sm" c="dimmed" maw={720}>
            {ACTIVE.has(run.status)
              ? 'Прогон ещё идёт — числа доменов появятся, когда он закончится. Расход ниже — уже потраченное.'
              : 'Итога по доменам у этого прогона нет: он остановлен раньше, чем дошёл до проверки доменов.'}
          </Text>
        ) : (
          <DomainsBlock run={run} domains={domains} />
        )}
      </Stack>
    </Card>
  );
}

export function RunReportPage() {
  const address = useRunAddress();
  const { runId } = address;
  const report = useQuery({
    queryKey: ['runs', 'report', runId],
    queryFn: () => fetchRunReport(runId ?? 0),
    enabled: runId !== null,
    // Идущий прогон дописывает расход на каждом чекпоинте — отчёт обновляется сам,
    // пока прогон не кончился, как строка истории.
    refetchInterval: (query) => {
      const status = query.state.data?.run.status;
      return status !== undefined && ACTIVE.has(status) ? 5000 : false;
    },
  });

  const missing = missingRun(address, report.error);
  if (missing !== null) {
    return (
      <NoSuchRun back={address.back} said={missing} where="отчёт открывается по номеру прогона." />
    );
  }
  if (report.error) {
    return (
      <Alert color="red" title="Отчёт прогона не загрузился">
        {refusalOf(report.error)}
      </Alert>
    );
  }
  if (report.data === undefined) return <Loader aria-label="Загружаем отчёт прогона" m="md" />;

  const view = report.data;
  return (
    <Stack gap="lg">
      <Head report={view} address={address} />
      {view.domains !== null && (
        <RejectionsCard
          rejected={view.rejected}
          thresholds={view.thresholds}
          country={view.run.country}
        />
      )}
      <SpendCard
        spending={view.spending}
        estimated={view.run.estimated_units}
        free={view.free_requests}
        active={ACTIVE.has(view.run.status)}
      />
    </Stack>
  );
}
