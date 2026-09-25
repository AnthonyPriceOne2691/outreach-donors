/**
 * Обзор: кто вошёл, что молчит, где мы и что ждёт человека.
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
import { countryTitle, permissionTitle, RUN_STATUSES } from '../api/labels';
import { fetchOverview } from '../api/overview';
import { fetchWatchdog } from '../api/settings';
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
import { Metric } from '../components/Metric';
import { formatDateTime, formatNumber, formatShare, formatUsd } from '../format';

/**
 * Сторож тишины на главной, а не в отдельном разделе: поломка этого
 * класса не показывает себя нигде, и человек не пойдёт её искать —
 * он увидит, что «всё тихо», и закроет вкладку.
 */
function Watchdog() {
  const { can } = useSession();
  const { data, isLoading } = useQuery({
    queryKey: ['watchdog'],
    queryFn: fetchWatchdog,
    enabled: can('view'),
    // Тревоги меняются часами, а не секундами: спрашивать чаще незачем.
    refetchInterval: 5 * 60 * 1000,
  });

  if (!can('view')) return null;

  const alarms = data?.alarms ?? [];
  // Ожидание — внутри карточки, а не одиноким кружком над ней.
  if (isLoading || alarms.length === 0) {
    return (
      <Card className="glass" p="lg">
        <Group gap="xs" mb={4}>
          <Title order={5}>Сторож тишины</Title>
          {isLoading && <Loader aria-label="Смотрим, что молчит" size="xs" />}
        </Group>
        <Text size="sm" c="dimmed">
          {isLoading
            ? 'Проверяем, что письма, ответы и фоновые проходы идут как ожидается.'
            : 'Тихо и правильно: письма, ответы и фоновые проходы идут как ожидается. Сторож отличает «ничего не происходит» от «мы перестали слышать» — и молчит только в первом случае.'}
        </Text>
      </Card>
    );
  }

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
      <Group justify="space-between" align="center" mb="md" gap="xs">
        <Group gap="sm" align="center">
          <Title order={5}>{title}</Title>
          {aside}
        </Group>
        {/* Средний вес, а не обычный: тонкие бирюзовые штрихи на ярком месте
            стекла читались бледнее цвета, которым написаны, — замер дал
            4,03 : 1 при ядре буквы 6,3 : 1 (25.09.2026). */}
        {to !== undefined && (
          <Anchor component={Link} to={to} size="sm" fw={500}>
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
          to="/threads"
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
          to="/threads"
        />
        <Metric
          title="Спорные рекламодатели"
          value={formatNumber(waiting.advertisers)}
          hint={waiting.advertisers > 0 ? 'решает человек' : 'спорных нет'}
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

function DonorsSection({ donors }: { donors: OverviewDonors }) {
  const checked = donors.total - donors.unchecked;
  return (
    <Section title="Доноры" to="/donors" toTitle="Все доноры">
      <SimpleGrid cols={{ base: 2, sm: 4, lg: 7 }} spacing="sm" className="metricGrid">
        <Metric
          title="В базе"
          value={formatNumber(donors.total)}
          hint={
            donors.unchecked > 0
              ? `не проверено ${formatNumber(donors.unchecked)}`
              : 'все проверены'
          }
        />
        <Metric
          title="Прошли пороги"
          value={formatNumber(donors.suitable)}
          hint={checked > 0 ? `${formatShare(donors.suitable / checked)} проверенных` : undefined}
        />
        {/* «Принял человек», а не «Приняты»: на «Отборе» «Приняты» — вкладка
            судьи и порогов, и одно слово с двумя числами на двух экранах
            читалось бы как расхождение. */}
        <Metric
          title="Принял человек"
          value={formatNumber(donors.accepted)}
          hint={
            donors.rejected > 0
              ? `отклонено ${formatNumber(donors.rejected)}`
              : donors.accepted > 0
                ? 'отклонённых нет'
                : 'решений не было'
          }
        />
        <Metric
          title="С адресом"
          value={formatNumber(donors.with_email)}
          hint={donors.form_only > 0 ? `и ${formatNumber(donors.form_only)} с формой` : undefined}
        />
        <Metric title="Написали" value={formatNumber(donors.written)} hint="хоть одно письмо" />
        <Metric
          title="Ответили"
          value={formatNumber(donors.replied)}
          hint={
            donors.written > 0
              ? `${formatShare(donors.replied / donors.written)} написанных`
              : 'писем ещё не было'
          }
        />
        <Metric title="С ценой" value={formatNumber(donors.priced)} hint={priceHint(donors)} />
      </SimpleGrid>
    </Section>
  );
}

function LettersSection({
  donors,
  advertisers,
  transport,
}: {
  donors: OverviewLetters;
  advertisers: OverviewLetters;
  transport: LetterTransport;
}) {
  const quietStageTwo = advertisers.queued === 0 && advertisers.sent === 0;
  return (
    <Section
      title="Письма донорам"
      to="/letters"
      toTitle="К письмам"
      aside={
        // Слово то же, что на экране писем: «не подключена» — состояние
        // до рабочего сервера, а не поломка.
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
        {!transport.real && (
          <Text size="sm" c="dimmed">
            Почта подключается на рабочем сервере: до этого письма собираются и правятся, но наружу
            не уходят.
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
          <Text size="sm">
            Юниты Ahrefs с начала месяца: <b>{formatNumber(data.ahrefs_units)}</b> из{' '}
            {formatNumber(data.ahrefs_cap)}
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
      <DonorsSection donors={data.donors} />
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
        <LettersSection
          donors={data.letters.donors}
          advertisers={data.letters.advertisers}
          transport={data.transport}
        />
        <RunSection data={data} />
      </SimpleGrid>
    </>
  );
}

export function OverviewPage() {
  const { user, can } = useSession();

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          <Title order={3}>Обзор</Title>
          <Text>
            Вошли как <b>{user?.email}</b>.{' '}
            <Anchor component={Link} to="/password" size="sm">
              Сменить пароль
            </Anchor>
          </Text>
          <Group gap="xs">
            <Text size="sm" c="dimmed">
              Доступные действия:
            </Text>
            {user?.permissions.map((permission) => (
              <Badge key={permission} variant="light">
                {permissionTitle(permission)}
              </Badge>
            ))}
          </Group>
        </Stack>
      </Card>

      <Watchdog />

      {can('view') && <Dashboard />}
    </Stack>
  );
}
