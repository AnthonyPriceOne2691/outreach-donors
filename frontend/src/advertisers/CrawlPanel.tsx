/**
 * Обход доноров — начало Этапа 2 на экране.
 *
 * До этой панели обход шёл только из консоли (`outreach crawl … --save`),
 * а рекламодатели появлялись на экране без ответа на вопрос «откуда».
 *
 * **Обходят только доноров со свежей ценой.** Оффер рекламодателю строится
 * на «мы дешевле», и дешевле чего — знает только цена донора. Кого нельзя —
 * числами над таблицей и словами сервера, что делать.
 *
 * **Ход — «340 из 1000», а не крутилка.** Обход идёт до получаса; пока он
 * идёт, панель спрашивает сервер сама, и по числу страниц видно, что он жив.
 * Остановленный разбором зависших — красным и с причиной за значком «!».
 *
 * **Кандидаты считаются сами** после обхода (задача пересчёта): когда идущих
 * не осталось, очередь рекламодателей ниже перечитывается.
 *
 * **Цена — с валютой, ручная — с пометкой** (07.10.2026). Конвертации нет, и
 * «150» без валюты читалось долларами, даже названное в евро. Цену, которую
 * агентство знает само, заводят здесь же — «Завести донора вручную»
 * (`ManualDonor`), и в таблице она помечена «вручную»: на ней держится «мы
 * дешевле», и цена со слов человека видна как таковая.
 *
 * **Пока обход не идёт, панель — одна строка** (аудит экранов 09.10.2026):
 * заголовок, «i» с пояснением, «можно обойти N» и кнопки. Таблица доноров и
 * последние обходы — под «Доноры и обходы»: панель стояла над спорными
 * рекламодателями, ради которых экран, целиком при каждом заходе. Идёт обход
 * или остановлен и ждёт человека — раскрыта сама. Обходить некого — почему,
 * сказано строкой сразу. «Завести донора вручную» — в окне, а не формой,
 * раздвигающей панель.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconChevronDown } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { refusalOf } from '../api/client';
import type { CrawlLaunch, CrawlsView, CrawlTarget, CrawlTargets } from '../api/crawls';
import { fetchCrawlTargets, fetchCrawls, startCrawls } from '../api/crawls';
import { useSession } from '../auth/AuthProvider';
import { formatDateTime, formatNumber } from '../format';
import { InfoHint } from '../components/InfoHint';
import { Seams } from '../components/Seams';
import { Unfold } from '../runs/Unfold';
import { CrawlState, TargetsTable, isActive } from './CrawlTable';
import { ManualDonor } from './ManualDonor';

export const CRAWL_TARGETS_KEY = ['crawl-targets'] as const;
export const CRAWLS_KEY = ['crawls'] as const;

/** Пока обход идёт, сервер спрашивается раз в десять секунд: страница
 *  донора — около полутора секунд, за десять их набегает пачка. */
const POLL_MS = 10_000;

/** Что вышло из нажатия — одной строкой, с причинами по каждому непоставленному. */
export function launchSummary(done: CrawlLaunch): string {
  const parts = [`Поставлено: ${formatNumber(Object.keys(done.queued).length)}.`];
  if (done.busy.length > 0) parts.push(`Уже идут: ${done.busy.join(', ')}.`);
  for (const [host, why] of Object.entries(done.refused)) parts.push(`${host}: ${why}.`);
  if (done.failed.length > 0) {
    parts.push(`Очередь не ответила: ${done.failed.join(', ')} — попробуйте ещё раз.`);
  }
  return parts.join(' ');
}

function Skipped({
  noPrice,
  stale,
  supplier,
}: {
  noPrice: number;
  stale: number;
  supplier: number;
}) {
  const parts = [
    noPrice > 0 ? `без цены — ${formatNumber(noPrice)}` : null,
    stale > 0 ? `цена протухла — ${formatNumber(stale)}` : null,
    supplier > 0 ? `поставщики — ${formatNumber(supplier)}` : null,
  ].filter((part) => part !== null);
  if (parts.length === 0) return null;
  return (
    <Text size="sm" c="dimmed">
      Не обходим: {parts.join(', ')}.
    </Text>
  );
}

function RecentCrawls({ view }: { view: CrawlsView | undefined }) {
  const rows = view?.rows.slice(0, 5) ?? [];
  if (rows.length === 0) return null;
  return (
    <Stack gap={4}>
      <Text size="sm" fw={600}>
        Последние обходы
      </Text>
      {/* На телефоне строка складывается в ярусы — домен, значок, дата, —
          а не ужимает значок до «идё…» (06.10.2026). */}
      {rows.map((row) => (
        <Group key={row.id} gap="xs">
          <Text size="sm" className="cellName">
            <Seams text={row.host} />
          </Text>
          <CrawlState crawl={row} />
          <Text size="xs" c="dimmed">
            {formatDateTime(row.finished_at ?? row.created_at)}
            {row.requested_by ? ` · ${row.requested_by}` : ''}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

/** Доноры и последние обходы — с опросом, пока что-то идёт. Кончился обход —
 *  кандидаты по нему считаются, и очередь рекламодателей ниже перечитывается,
 *  а не ждёт перезагрузки страницы. */
function useCrawlData() {
  const queryClient = useQueryClient();
  const targets = useQuery({
    queryKey: CRAWL_TARGETS_KEY,
    queryFn: fetchCrawlTargets,
    refetchInterval: (query) =>
      query.state.data?.donors.some((donor) => isActive(donor.crawl)) ? POLL_MS : false,
  });
  const recent = useQuery({
    queryKey: CRAWLS_KEY,
    queryFn: fetchCrawls,
    refetchInterval: (query) => ((query.state.data?.active ?? 0) > 0 ? POLL_MS : false),
  });
  const active = recent.data?.active ?? 0;
  const before = useRef(active);
  useEffect(() => {
    if (active < before.current) {
      void queryClient.invalidateQueries({ queryKey: ['advertisers'] });
      void queryClient.invalidateQueries({ queryKey: CRAWL_TARGETS_KEY });
    }
    before.current = active;
  }, [active, queryClient]);
  return { targets, recent };
}

function useStartCrawls(onQueued: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (hosts: string[]) => startCrawls(hosts),
    onSuccess: async (done) => {
      onQueued();
      await queryClient.invalidateQueries({ queryKey: CRAWL_TARGETS_KEY });
      await queryClient.invalidateQueries({ queryKey: CRAWLS_KEY });
      const clean = done.failed.length === 0 && Object.keys(done.refused).length === 0;
      notifications.show({ message: launchSummary(done), color: clean ? 'green' : 'yellow' });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не поставили', message: refusalOf(failure), color: 'red' }),
  });
}

/** Чего панели не хватает: доноры не загрузились, обходчиков нет. */
function Troubles({ error, workers }: { error: unknown; workers: number | null | undefined }) {
  return (
    <>
      {error ? (
        <Alert color="red" title="Доноры не загрузились">
          {refusalOf(error)}
        </Alert>
      ) : null}
      {workers === 0 ? (
        <Alert color="yellow" title="Обходчиков нет">
          Поставленные обходы ждут в очереди, и брать их некому. Поднять: docker compose up -d
          crawler
        </Alert>
      ) : null}
    </>
  );
}

/** Что панель делает — в «i» у заголовка, а не абзацем над таблицей. */
function about(data: CrawlTargets | undefined): string {
  return (
    `Этап 2 начинается здесь: обходим доноров с известной и свежей ценой — до ` +
    `${formatNumber(data?.max_pages ?? 1000)} страниц на донора — и ищем, кому они ставят ` +
    'ссылки. Кандидаты в рекламодатели считаются сами, когда обход закончится.'
  );
}

/** Что с обходом сейчас — строкой у заголовка: идёт, остановлен, сколько можно обойти. */
function Facts({ donors }: { donors: CrawlTarget[] }) {
  const running = donors.filter((donor) => isActive(donor.crawl)).length;
  const stopped = donors.filter((donor) => donor.crawl?.status === 'stopped').length;
  return (
    <Group gap="xs" align="center">
      {running > 0 && (
        <Badge variant="light" color="blue">
          идёт: {formatNumber(running)}
        </Badge>
      )}
      {stopped > 0 && (
        <Badge variant="light" color="red">
          остановлено: {formatNumber(stopped)}
        </Badge>
      )}
      <Text size="sm" c="dimmed">
        {donors.length > 0 ? `можно обойти: ${formatNumber(donors.length)}` : 'обходить некого'}
      </Text>
    </Group>
  );
}

/** Обход идёт или остановлен и ждёт человека — тогда панель раскрыта сама. */
function needsEye(donors: CrawlTarget[]): boolean {
  return donors.some((donor) => isActive(donor.crawl) || donor.crawl?.status === 'stopped');
}

/** Кого обходим: таблица с отметками или, если некого, — что делать сначала. */
function Targets({
  data,
  picked,
  onPick,
  mayStart,
}: {
  data: CrawlTargets;
  picked: Set<string>;
  onPick: (host: string, on: boolean) => void;
  mayStart: boolean;
}) {
  return (
    <>
      <Skipped noPrice={data.no_price} stale={data.stale_price} supplier={data.supplier} />
      {data.donors.length === 0 ? (
        <Stack gap={4}>
          {data.notes.map((note) => (
            <Text key={note} size="sm">
              {note}
            </Text>
          ))}
        </Stack>
      ) : (
        <TargetsTable donors={data.donors} picked={picked} onPick={onPick} mayStart={mayStart} />
      )}
    </>
  );
}

function togglePicked(was: Set<string>, host: string, on: boolean): Set<string> {
  const next = new Set(was);
  if (on) next.add(host);
  else next.delete(host);
  return next;
}

const MANUAL_FORM_ID = 'manual-donor';
const LIST_ID = 'crawl-list';

/** Строка заголовка: «i», что с обходом сейчас и две кнопки — список и ручной донор. */
function Head({
  data,
  listed,
  shown,
  onToggle,
  onEnter,
}: {
  data: CrawlTargets | undefined;
  /** Есть что раскрывать: доноры для обхода или последние обходы. */
  listed: boolean;
  shown: boolean;
  onToggle: () => void;
  /** Завести донора вручную; `null` — нет права запуска. */
  onEnter: (() => void) | null;
}) {
  return (
    <Group justify="space-between" align="center" gap="sm">
      <Group gap="sm" align="center">
        <Title order={3}>Обход доноров</Title>
        <InfoHint name="Как устроен обход доноров" width={360}>
          {about(data)}
        </InfoHint>
        {data ? <Facts donors={data.donors} /> : null}
      </Group>
      <Group gap="xs">
        {listed ? (
          <Button
            variant="subtle"
            className="press"
            rightSection={
              <IconChevronDown
                size={16}
                style={{
                  transform: shown ? 'rotate(180deg)' : 'none',
                  transition: 'transform 200ms cubic-bezier(0.32, 0.72, 0, 1)',
                }}
              />
            }
            aria-expanded={shown}
            aria-controls={LIST_ID}
            onClick={onToggle}
          >
            Доноры и обходы
          </Button>
        ) : null}
        {/* «Завести донора вручную» — и когда обходить некого: тогда он нужнее
            всего («сначала прогон Этапа 1 — или донор, заведённый вручную»). */}
        {onEnter !== null ? (
          <Button variant="default" className="press" aria-haspopup="dialog" onClick={onEnter}>
            Завести донора вручную
          </Button>
        ) : null}
      </Group>
    </Group>
  );
}

/** Под «Доноры и обходы»: кого обойти, кнопка запуска, последние обходы. */
function CrawlList({
  data,
  picked,
  onPick,
  mayStart,
  start,
  recent,
}: {
  data: CrawlTargets | undefined;
  picked: Set<string>;
  onPick: (host: string, on: boolean) => void;
  mayStart: boolean;
  start: ReturnType<typeof useStartCrawls>;
  recent: CrawlsView | undefined;
}) {
  const donors = data?.donors ?? [];
  return (
    <Stack gap="sm" id={LIST_ID}>
      {data && donors.length > 0 ? (
        <Targets data={data} picked={picked} onPick={onPick} mayStart={mayStart} />
      ) : null}
      {mayStart && donors.length > 0 ? (
        <Group>
          <Button
            className="press"
            disabled={picked.size === 0}
            loading={start.isPending}
            onClick={() => start.mutate([...picked])}
          >
            Обойти отмеченных · {formatNumber(picked.size)}
          </Button>
        </Group>
      ) : null}
      <RecentCrawls view={recent} />
    </Stack>
  );
}

/** Доноры, обходы и что из них следует для панели — одной выкладкой. */
function useCrawlView() {
  const { targets, recent } = useCrawlData();
  const donors = targets.data?.donors ?? [];
  return {
    targets,
    recent,
    /** Обходить некого — почему, сказано сразу: это и есть то, что делать дальше. */
    nobody: targets.data !== undefined && donors.length === 0,
    listed: donors.length > 0 || (recent.data?.rows.length ?? 0) > 0,
    attention: needsEye(donors),
  };
}

export function CrawlPanel() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [entering, setEntering] = useState(false);
  // `null` — руками не трогали: раскрыта, пока обход идёт или ждёт человека.
  const [opened, setOpened] = useState<boolean | null>(null);
  const mayStart = can('run');
  const { targets, recent, nobody, listed, attention } = useCrawlView();
  const start = useStartCrawls(() => setPicked(new Set()));
  const data = targets.data;
  const shown = opened ?? attention;
  const onPick = (host: string, on: boolean) => setPicked((was) => togglePicked(was, host, on));

  return (
    <Card className="glassPanel crawlPanel" p="xl">
      <Stack gap="sm">
        <Head
          data={data}
          listed={listed}
          shown={shown}
          onToggle={() => setOpened(!shown)}
          onEnter={mayStart ? () => setEntering(true) : null}
        />
        {targets.isLoading ? <Loader size="sm" aria-label="Загружаем доноров" /> : null}
        <Troubles error={targets.error} workers={data?.workers} />
        {data && nobody ? (
          <Targets data={data} picked={picked} onPick={onPick} mayStart={mayStart} />
        ) : null}
        <Unfold open={shown && listed}>
          <CrawlList
            data={data}
            picked={picked}
            onPick={onPick}
            mayStart={mayStart}
            start={start}
            recent={recent.data}
          />
        </Unfold>
      </Stack>
      <Modal
        opened={entering}
        onClose={() => setEntering(false)}
        title="Завести донора вручную"
        size="lg"
      >
        <ManualDonor
          id={MANUAL_FORM_ID}
          onEntered={() => {
            setEntering(false);
            void queryClient.invalidateQueries({ queryKey: CRAWL_TARGETS_KEY });
            void queryClient.invalidateQueries({ queryKey: ['donors'] });
          }}
          onCancel={() => setEntering(false)}
        />
      </Modal>
    </Card>
  );
}
