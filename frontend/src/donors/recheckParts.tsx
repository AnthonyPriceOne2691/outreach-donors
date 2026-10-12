/**
 * Части окон перезапроса цены — общие у карточки донора (`PriceRecheck`) и панели обхода
 * (`StalePrices`): отказ словами сервера, итог нажатия и переписка, куда уйдёт письмо.
 *
 * Отказ в окнах перезапроса один на три места — не загрузилось окно карточки,
 * не загрузился список панели, не ушёл перезапрос — и говорит одинаково: красным,
 * заголовком «что не вышло» и словами сервера под ним.
 */

import { Alert, Anchor } from '@mantine/core';
import { Link } from 'react-router-dom';

import { refusalOf } from '../api/client';
import type { RecheckResult } from '../api/priceRecheck';
import { recheckSummary } from '../api/priceRecheck';

/** Отказ — красным: заголовок — что не вышло, под ним — слова сервера. */
export function Refused({ title, error }: { title: string; error: unknown }) {
  return (
    <Alert color="red" title={title}>
      {refusalOf(error)}
    </Alert>
  );
}

/** Итог нажатия — в окне: всё ушло — зелёным, что-то нет — янтарным, отказ — красным. */
export function RecheckOutcome({
  done,
  error,
}: {
  done: RecheckResult | undefined;
  error: unknown;
}) {
  if (error) return <Refused title="Не перезапросили" error={error} />;
  if (done === undefined) return null;
  return (
    <Alert color={done.refused.length === 0 ? 'green' : 'yellow'}>{recheckSummary(done)}</Alert>
  );
}

/** Переписка, куда уйдёт перезапрос, — ссылкой. */
export function ThreadLink({ threadId }: { threadId: number }) {
  return (
    <Anchor component={Link} to={`/threads/${threadId}`} size="sm">
      переписку №{threadId}
    </Anchor>
  );
}
