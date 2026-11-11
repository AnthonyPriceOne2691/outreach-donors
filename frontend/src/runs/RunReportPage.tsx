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
 *
 * **На виду — факты, пояснения — в «i»** (слово владельца 10.11.2026; было решение
 * 20.10.2026 держать их абзацами — четыре подряд, 114 слов). Путь доменов и числа стоят
 * строкой и плитками; почему «Рассмотреть» не равно «Подходит» — в «i» у кнопки, что
 * за пороги и кто исключён — в «i» у заголовков карточек (`RejectionsCard`, `ReasonsCard`).
 * Путь доменов — тоже итог строкой, «из выдачи N, новых в проверку M», а кого по дороге
 * исключили и кто проверен раньше — в «i» у строки (`Funnel`, проверка прода 10.11.2026).
 */

import { Alert, Badge, Card, Group, Loader, SimpleGrid, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { competitorsOf, runInput } from '../api/competitors';
import { countryTitle, DONOR_STATUSES, RUN_STATUSES } from '../api/labels';
import { fetchRunReport } from '../api/runReport';
import type { RunDomains, RunReportView } from '../api/runReport';
import type { RunCard, RunStatus } from '../api/types';
import { InfoHint } from '../components/InfoHint';
import { Metric } from '../components/Metric';
import { formatDateTime, formatNumber, plural } from '../format';
import { CompetitorJournal } from './CompetitorJournal';
import { ReasonsCard } from './ReasonsCard';
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

/** Путь домена до вердикта: из выдачи (или ссылок конкурентов) — мимо гейта и свежих — в
 *  Ahrefs. На виду — итог одной строкой: сколько пришло и сколько новых ушло в проверку;
 *  разбивка — исключённые и проверенные раньше — в «i» окошком, теми же словами и числами
 *  (проверка прода 10.11.2026: строка с разбивкой стояла абзацем — две строки на 1440 и пять
 *  на 390). Отчёт записан до числа найденных — итог без него. Число не отрывается от слова
 *  и от тире — неразрывные пробелы. Свежих прогон с 20.10.2026 судит сам — своей страной и
 *  порогами, без Ahrefs. */
function Funnel({ domains, from }: { domains: RunDomains; from: string }) {
  const count = domains.found;
  const found =
    count === null ? from : `${from} — ${formatNumber(count)}\u00a0${domainsWord(count)}`;
  const fresh = domains.fresh_judged
    ? 'метрики в сроке: вердикт этого прогона по сохранённым, платить не пришлось'
    : 'метрики в сроке, платить не пришлось';
  const toCheck = `новых в проверку Ahrefs —\u00a0${formatNumber(domains.to_check)}`;
  return (
    <Group gap={4} wrap="nowrap" align="center">
      <Text size="sm" style={{ minWidth: 0 }}>
        {found}
        {count === null ? ': ' : ', '}
        {toCheck}
      </Text>
      <InfoHint name="Путь доменов: исключённые и проверенные раньше" width={320} interactive>
        <Text size="sm">
          {found}: исключено {formatNumber(domains.excluded)}, проверены раньше{' '}
          {formatNumber(domains.fresh)} — {fresh}; {toCheck}.
        </Text>
      </InfoHint>
    </Group>
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

/** Почему «Рассмотреть» у прогона не равно «Подходит» (ревью 20.10.2026): «Подходит» — вердикт
 *  прогона, а «Рассмотреть» — кто ждёт решения сейчас. Решённые туда не входят, «кому не пишем»
 *  в очередь не кладут, а взятые из резерва — сверх «Подходит». В «i» у кнопки «Рассмотреть», а
 *  не строкой под плитками (слово владельца 10.11.2026: четыре абзаца подряд — итог строкой
 *  и «i»). Числа сходятся — значка нет. */
function QueueNote({ run, domains }: { run: RunCard; domains: RunDomains | null }) {
  if (domains === null || (run.queue.pending ?? 0) === domains.suitable) return null;
  return (
    <InfoHint name="Почему «Рассмотреть» не равно «Подходит»" width={300} interactive>
      <Text size="sm">
        «Рассмотреть» — кто ждёт решения сейчас, поэтому не равно «Подходит»: решённые человеком в
        него не входят, «кому не пишем» в очередь не кладут, а взятые из резерва — сверх.
      </Text>
    </InfoHint>
  );
}

/** Факты прогона строкой: состояние, страна, ключи, когда запущен — и вход в рассмотрение. */
function Facts({ run, from, domains }: { run: RunCard; from: string; domains: RunDomains | null }) {
  const pending = run.queue.pending ?? 0;
  const queued = pending + (run.queue.accepted ?? 0) + (run.queue.rejected ?? 0);
  return (
    <Group gap="sm">
      <Badge variant="light" color={RUN_STATUSES[run.status].color}>
        {RUN_STATUSES[run.status].title}
      </Badge>
      <Text size="sm">
        {countryTitle(run.country)},{' '}
        {runInput({ keywords: run.keywords, competitors: competitorsOf(run) })}, запущен{' '}
        {formatDateTime(run.started_at)}
        {/* Кто — по записи журнала о запуске; прогон из консоли её не оставляет. */}
        {run.started_by ? `, запустил ${run.started_by}` : ' — кто запустил, не записано'}
      </Text>
      {queued > 0 && (
        <Group gap={4} wrap="nowrap">
          <ReviewLink run={run} from={from}>
            {pending > 0 ? `Рассмотреть ${formatNumber(pending)}` : 'Рассмотрение'}
          </ReviewLink>
          <QueueNote run={run} domains={domains} />
        </Group>
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
      <Funnel
        domains={domains}
        from={competitorsOf(run).length > 0 ? 'Из ссылок конкурентов' : 'Из выдачи'}
      />
      <CompetitorJournal run={run} />
      <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
        <Metric
          title="Проверено в Ahrefs"
          value={formatNumber(domains.checked)}
          hint={domains.fresh_judged ? 'новых, с любым вердиктом' : 'с любым вердиктом'}
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
          hint={domains.fresh_judged ? 'данных не хватило' : 'Ahrefs не дал данных'}
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
          <Facts run={run} from={address.from} domains={domains} />
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
          freshJudged={view.domains.fresh_judged}
          runId={view.run.id}
        />
      )}
      {view.domains !== null && <ReasonsCard excluded={view.excluded} unchecked={view.unchecked} />}
      <SpendCard
        spending={view.spending}
        estimated={view.run.estimated_units}
        free={view.free_requests}
        active={ACTIVE.has(view.run.status)}
      />
    </Stack>
  );
}
