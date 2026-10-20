/**
 * «Доноры» и «Резерв» — две вкладки одного раздела (резерв, 20.10.2026).
 *
 * Резерв — домены прогонов, которые не прошли: до него они жили только здесь же, на
 * «Донорах», под «Решение: без решения» и вердиктом «не подходит», — без прогона, без
 * причины словами и без способа взять такой домен на рассмотрение. Вкладка — адресом
 * (`/donors` и `/donors/reserve`): ссылка из отчёта и очереди прогона ведёт сразу в
 * резерв прогона, а «назад» возвращает туда, откуда пришли.
 */

import { SegmentedControl } from '@mantine/core';
import { useNavigate } from 'react-router-dom';

export type DonorsTab = 'donors' | 'reserve';

const PATHS: Record<DonorsTab, string> = { donors: '/donors', reserve: '/donors/reserve' };

export function DonorsTabs({ tab }: { tab: DonorsTab }) {
  const navigate = useNavigate();
  return (
    <SegmentedControl
      aria-label="Раздел доноров"
      value={tab}
      onChange={(value) => {
        const next: DonorsTab = value === 'reserve' ? 'reserve' : 'donors';
        if (next !== tab) void navigate(PATHS[next]);
      }}
      data={[
        { value: 'donors', label: 'Доноры' },
        { value: 'reserve', label: 'Резерв' },
      ]}
      style={{ alignSelf: 'flex-start' }}
    />
  );
}
