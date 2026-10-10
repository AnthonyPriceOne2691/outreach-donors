/**
 * Обзор: что молчит, где мы и что ждёт человека.
 *
 * Главная отвечает числами, а не описанием разделов (замечание 25.09.2026):
 * карта сервиса из шести плиток-пояснений читалась один раз, а открывают
 * главную каждый день — и каждый раз за тем, чтобы узнать, что изменилось
 * и что делать. Поэтому сверху работа для человека, ниже — доноры, письма
 * и расход. Каждое число посчитано сервером тем же правилом, что на своём
 * экране: своё правило на фронте разошлось бы с экраном при первой правке.
 */

import {
  Alert,
  Anchor,
  Badge,
  Card,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { countryTitle, RUN_STATUSES } from '../api/labels';
import { fetchOverview } from '../api/overview';
import { fetchWatchdog } from '../api/settings';
import { stageShown } from '../api/stages';
import type {
  LetterTransport,
  OverviewDonors,
  OverviewLetters,
  OverviewView,
  OverviewWaiting,
  RunCard,
} from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { Meter } from '../components/Meter';
import { PageHead } from '../components/PageHead';
import { Metric } from '../components/Metric';
import { formatDateTime, formatNumber, formatShare, formatUsd, plural } from '../format';
import { mailLine } from '../letters/mailTile';

/**
 * Тревоги сторожа тишины — на главной, а не в отдельном разделе: поломка
 * этого класса не показывает себя нигде, и человек не пойдёт её искать.
 *
 * **Когда тихо, сторожа на экране нет.** Карточка «Сторож тишины» с текстом
 * «тихо и правильно» стояла на главной всегда и ничего не сообщала
 * (замечание 25.09.2026: «убрать плашку сторожа с главной»). Сторож
 * по-прежнему спрашивается каждые пять минут, и тревога встаёт красной
 * полосой над сводкой — появление полосы и есть сигнал.
 */
function Watchdog() {
  const { can } = useSession();
  const { data } = useQuery({
    queryKey: ['watchdog'],
    queryFn: fetchWatchdog,
    enabled: can('view'),
    // Тревоги меняются часами, а не секундами: спрашивать чаще незачем.
    refetchInterval: 5 * 60 * 1000,
  });

  // Тревога о почте продаж — только с правом «Продажи» (П2б), даже пришедшая раньше,
  // чем право сняли; общая тревога (`stage: null`) — всем.
  const alarms = (data?.alarms ?? []).filter(
    (alarm) => alarm.stage === null || stageShown(alarm.stage, can('sales')),
  );
  if (!can('view') || alarms.length === 0) return null;

  return (
    <Stack gap="sm">
      {alarms.map((alarm) => (
        <Alert key={alarm.code} color="red" title={alarm.title}>
          {alarm.detail}
        </Alert>
      ))}
    </Stack>
  );
}

/** Карточка раздела сводки: заголовок, ссылка на экран и содержимое. */
function Section({
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
function attention(count: number): string | undefined {
  return count > 0 ? 'yellow' : undefined;
}

/** Части числа меню: спорные и бизнесы ниши ждут «пишем / не пишем» (проверка прода 10.10.2026). */
function advertisersHint({ advertisers, niche }: OverviewWaiting): string {
  const parts = [
    advertisers > niche ? `спорных ${formatNumber(advertisers - niche)}` : '',
    niche > 0 ? `бизнесов ниши ${formatNumber(niche)}` : '',
  ].filter(Boolean);
  return parts.length > 0 ? parts.join(' · ') : 'решений не ждут';
}

function WaitingSection({ waiting }: { waiting: OverviewWaiting }) {
  const newest = waiting.review_runs[0];
  return (
    <Section title="Ждут человека">
      <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }} spacing="sm" className="metricGrid">
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
          // прятал нужные среди сотни остальных (аудит 25.09.2026, №25).
          to="/threads?state=needs_review"
        />
        <Metric
          title="Заполнить формы"
          value={formatNumber(waiting.forms)}
          hint={waiting.forms > 0 ? 'у сайта форма вместо адреса' : 'форм в очереди нет'}
          color={attention(waiting.forms)}
          to="/forms"
        />
        <Metric
          title="Лиды рекламодателей"
          value={formatNumber(waiting.leads)}
          hint={waiting.leads > 0 ? 'ответили на оффер' : 'новых лидов нет'}
          color={attention(waiting.leads)}
          to="/threads?state=lead"
        />
        <Metric
          title="Кандидаты в рекламодатели"
          value={formatNumber(waiting.advertisers)}
          hint={advertisersHint(waiting)}
          color={attention(waiting.advertisers)}
          to="/advertisers"
        />
      </SimpleGrid>
    </Section>
  );
}

function priceHint(donors: OverviewDonors): string {
  if (donors.priced === 0) return 'цен ещё нет';
  if (donors.priced_fresh === donors.priced) return 'все цены свежие';
  return `свежих ${formatNumber(donors.priced_fresh)}`;
}

/**
 * Под «Ответили»: доля написанных — или почему её нет.
 *
 * «Написали» считает принятых доноров (как вся воронка), а «Ушло» в письмах
 * донорам — все письма этапа, и доменам, которых человек не принял или
 * отклонил потом. Ноль в воронке рядом с «Ушло 24» поэтому не значит, что
 * писем не было: до 10.10.2026 здесь стояло «писем ещё не было» — неправда
 * на том же экране (проверка QA 10.10.2026). Так говорится, только когда
 * донорам не ушло ни одного письма.
 */
function repliedHint(donors: OverviewDonors, sent: number): string {
  if (donors.written > 0) return `${formatShare(donors.replied / donors.written)} написанных`;
  return sent > 0 ? 'принятым донорам не писали' : 'писем ещё не было';
}

/**
 * Воронка: от проверенного домена до донора с ценой (решение 26.09.2026).
 *
 * «Донор» здесь значит одно — домен, принятый человеком: то же, что на экране
 * «Доноры», куда ведёт плитка. Записей в базе больше — у каждого домена, за
 * чьи метрики заплатил прогон, — и это «проверено доменов», а не доноры:
 * «доноров 1 065» над пустым списком доноров читалось бы как сбой. Всё, что
 * ниже «Доноров», считается среди них — тем же правилом, что у списка.
 */
function DonorsSection({ donors, sent }: { donors: OverviewDonors; sent: number }) {
  const checked = donors.total - donors.unchecked;
  return (
    // Ссылки у раздела нет: «Доноры» — плитка-ссылка внутри, и две ссылки
    // на один экран в одном разделе читались бы как два разных места.
    <Section title="Воронка доноров">
      <SimpleGrid cols={{ base: 2, sm: 4, lg: 7 }} spacing="sm" className="metricGrid">
        <Metric
          title="Проверено доменов"
          value={formatNumber(donors.total)}
          hint={donors.unchecked > 0 ? `не проверено ${formatNumber(donors.unchecked)}` : undefined}
        />
        <Metric
          title="Прошли пороги"
          value={formatNumber(donors.suitable)}
          hint={checked > 0 ? `${formatShare(donors.suitable / checked)} проверенных` : undefined}
        />
        {/* «Доноры» — принятые человеком. Не «Приняты»: на «Отборе» «Приняты» —
            вкладка судьи и порогов, и одно слово с двумя числами на двух
            экранах читалось бы как расхождение. */}
        <Metric
          title="Доноры"
          value={formatNumber(donors.accepted)}
          hint={
            donors.rejected > 0
              ? `отклонено ${formatNumber(donors.rejected)}`
              : donors.accepted > 0
                ? 'отклонённых нет'
                : 'решений не было'
          }
          to="/donors"
        />
        {/* «С адресом» — адрес есть в базе: правило фильтра списка доноров и ступени писем
            (проверка прода 10.10.2026: здесь 22, там 23 при «Написали 23»). Письмо уходит
            только на адрес — «Написали» не больше; кончились ли адреса, скажут «Письма». */}
        <Metric
          title="С адресом"
          value={formatNumber(donors.with_email)}
          hint={donors.form_only > 0 ? `и ${formatNumber(donors.form_only)} с формой` : undefined}
          to="/donors?has_contact=true"
        />
        {/* Кому из принятых ушло хоть одно письмо — это и сказано под числом:
            «Ушло» ниже считает письма всем доменам, и ноль здесь рядом с ним
            читался сбоем (проверка QA 10.10.2026). */}
        <Metric title="Написали" value={formatNumber(donors.written)} hint="принятым донорам" />
        <Metric
          title="Ответили"
          value={formatNumber(donors.replied)}
          hint={repliedHint(donors, sent)}
        />
        <Metric title="С ценой" value={formatNumber(donors.priced)} hint={priceHint(donors)} />
      </SimpleGrid>
    </Section>
  );
}

/**
 * Ответы, не привязанные ни к одному нашему письму, — строкой под плитками
 * писем, и только когда они есть. Не плиткой в «Ждут человека»: убрать такой
 * ответ оттуда пока нечем (привязки руками нет), и янтарное число стояло бы
 * вечно — с первого же ответа на пробное письмо.
 */
function UnboundLine({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <Text size="sm">
      Не привязаны ни к одному нашему письму:{' '}
      {/* Чернилами и жирным, как числа в соседних строках сводки, — ссылку
          выдаёт подчёркивание. Бирюзовая на светлом месте стекла намерилась
          на 4,04 : 1 средним весом и 4,42 жирным при норме 4,5 (28.09.2026). */}
      <Anchor
        component={Link}
        to="/threads?tab=unbound"
        size="sm"
        fw={700}
        c="var(--ink)"
        underline="always"
        className="inkLink"
      >
        {formatNumber(count)} {plural(count, 'ответ', 'ответа', 'ответов')}
      </Anchor>
    </Text>
  );
}

function LettersSection({
  donors,
  advertisers,
  transport,
  unbound,
}: {
  donors: OverviewLetters;
  advertisers: OverviewLetters;
  transport: LetterTransport;
  unbound: number;
}) {
  const quietStageTwo = advertisers.queued === 0 && advertisers.sent === 0;
  const mail = mailLine(transport);
  return (
    <Section
      title="Письма донорам"
      to="/letters"
      toTitle="К письмам"
      aside={
        // Слово то же, что в плитке «Почта» на экране писем (`mailTile`):
        // «не подключена», а что с письмами — фразой под плитками.
        <Badge variant="light" color={transport.real ? 'green' : 'yellow'}>
          {transport.real ? 'почта подключена' : 'почта не подключена'}
        </Badge>
      }
    >
      <Stack gap="sm">
        <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
          <Metric title="В очереди" value={formatNumber(donors.queued)} />
          <Metric title="Ушло" value={formatNumber(donors.sent)} />
          <Metric title="Дошло" value={formatNumber(donors.delivered)} />
          {/* Слово и цвет — как у состояния диалога: отказ доставки требует
              внимания (адрес выбыл), но это не отказ донора. */}
          <Metric
            title="Отказ доставки"
            value={formatNumber(donors.bounced)}
            color={attention(donors.bounced)}
          />
        </SimpleGrid>
        <UnboundLine count={unbound} />
        {mail !== null && (
          <Text size="sm" c="dimmed">
            {mail}
          </Text>
        )}
        <Text size="sm" c="dimmed">
          {quietStageTwo
            ? 'Рекламодателям писем ещё не было.'
            : `Рекламодателям: в очереди ${formatNumber(advertisers.queued)}, ушло ${formatNumber(advertisers.sent)}, дошло ${formatNumber(advertisers.delivered)}.`}
        </Text>
      </Stack>
    </Section>
  );
}

/** Последний прогон: что запускали и чем кончился. Очередь рассмотрения
 *  здесь не повторяется — она в «Ждут человека», и два «Рассмотреть» на одном
 *  экране читаются как сбой, а не как забота. */
function LastRunLine({ run }: { run: RunCard | null }) {
  if (run === null) {
    return <Text size="sm">Прогонов ещё не было.</Text>;
  }
  const status = RUN_STATUSES[run.status];
  return (
    <Stack gap={4}>
      <Group gap="xs" align="center">
        <Text fw={600}>Прогон №{run.id}</Text>
        <Text>{countryTitle(run.country)}</Text>
        <Badge variant="light" color={status.color}>
          {status.title}
        </Badge>
      </Group>
      <Text size="sm" c="dimmed">
        {formatDateTime(run.started_at)} · ключей {formatNumber(run.keywords)} · доменов{' '}
        {formatNumber(run.hosts)} ·{' '}
        {run.actual_units !== null
          ? `потрачено ${formatNumber(run.actual_units)} юн.`
          : `смета ${formatNumber(run.estimated_units)} юн.`}
      </Text>
    </Stack>
  );
}

function RunSection({ data }: { data: OverviewView }) {
  return (
    <Section title="Прогон и расход" to="/run" toTitle="К прогонам">
      <Stack gap="md">
        <LastRunLine run={data.last_run} />
        <Stack gap={6} className="hairline" pt="md">
          {/* «Потрачено из потолка» — одна единица смысла: на телефоне строка
              рвалась между «из» и потолком (аудит 25.09.2026, №28). */}
          <Text size="sm">
            Юниты Ahrefs с начала месяца:{' '}
            <span style={{ whiteSpace: 'nowrap' }}>
              <b>{formatNumber(data.ahrefs_units)}</b> из {formatNumber(data.ahrefs_cap)}
            </span>
          </Text>
          <Meter
            spent={data.ahrefs_units}
            cap={data.ahrefs_cap}
            label="Юниты Ahrefs с начала месяца"
          />
          <Text size="sm" c="dimmed">
            По нашему капу. Остаток у провайдера — на экране{' '}
            <Anchor component={Link} to="/usage" size="sm">
              расхода
            </Anchor>
            .
          </Text>
        </Stack>
        <Text size="sm">
          Выдача с начала месяца: <b>{formatUsd(data.serp_usd)}</b>
        </Text>
      </Stack>
    </Section>
  );
}

function Dashboard() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['overview'],
    queryFn: fetchOverview,
    // Числа двигает работа людей и фоновых задач — минуты, а не секунды.
    refetchInterval: 60 * 1000,
  });

  if (isLoading) {
    return (
      <Card className="glass" p="lg">
        <Group gap="xs">
          <Loader aria-label="Считаем сводку" size="xs" />
          <Text size="sm" c="dimmed">
            Считаем сводку по базе.
          </Text>
        </Group>
      </Card>
    );
  }
  if (error) {
    return (
      <Alert color="red" title="Сводка не загрузилась">
        {refusalOf(error)}
      </Alert>
    );
  }
  if (data === undefined) return null;

  return (
    <>
      <WaitingSection waiting={data.waiting} />
      <DonorsSection donors={data.donors} sent={data.letters.donors.sent} />
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
        <LettersSection
          donors={data.letters.donors}
          advertisers={data.letters.advertisers}
          transport={data.transport}
          unbound={data.unbound_replies}
        />
        <RunSection data={data} />
      </SimpleGrid>
    </>
  );
}

/** Подсказка «i» у заголовка — как читать экран. */
const OVERVIEW_HINT =
  'Сверху — работа, которая ждёт человека: те же числа стоят у пунктов меню. Ниже — воронка доноров, письма и расход. Каждое число посчитано тем же правилом, что на экране раздела.';

export function OverviewPage() {
  const { can } = useSession();

  return (
    <Stack gap="lg">
      {/* Шапка — одним заголовком. Кто вошёл, его права и «Сменить пароль» — в меню
          у почты в шапке рамы, на любом экране (`AccountMenu`); до 09.10.2026 они
          стояли здесь строкой над «Ждут человека» (аудит экранов 09.10.2026). */}
      <Card className="glassPanel" px="xl" py="lg">
        <PageHead title="Обзор" hint={OVERVIEW_HINT} />
      </Card>

      <Watchdog />

      {can('view') && <Dashboard />}
    </Stack>
  );
}
