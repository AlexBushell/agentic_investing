from research_platform.sources.nsm import NSMCandidate, NSMDownloadService


def test_annual_report_selection_prefers_esef_popup_over_pdf_and_rns_wrapper():
    candidates = [
        NSMCandidate(
            title="Annual Financial Report",
            date_text="13/03/2026 13:12",
            organisation_name="THE GYM GROUP PLC",
            category="Annual Financial Report",
            href="https://data.fca.org.uk/artefacts/NSM/RNS/780a331e-f5ad-4244-a2af-fea9be4c7d76.html",
        ),
        NSMCandidate(
            title="Annual Report and Audited Accounts",
            date_text="13/03/2026 12:04",
            organisation_name="THE GYM GROUP PLC",
            category="Annual Financial Report",
            href="https://data.fca.org.uk/artefacts/NSM/DirectUpload/NI-000140729/NI-000140729.pdf",
        ),
        NSMCandidate(
            title="ESEF Tagged Annual Report and Audited Accounts",
            date_text="13/03/2026 11:58",
            organisation_name="THE GYM GROUP PLC",
            category="Annual Financial Report",
            href=None,
        ),
    ]

    selected = NSMDownloadService._select_candidate(candidates, "annual-report")

    assert selected is not None
    assert selected.title == "ESEF Tagged Annual Report and Audited Accounts"
    assert selected.href is None


def test_annual_history_grouping_selects_best_candidate_per_year():
    candidates = [
        NSMCandidate(
            title="Annual Report and Audited Accounts",
            date_text="13/03/2026 12:04",
            organisation_name="THE GYM GROUP PLC",
            category="Annual Financial Report",
            href="https://data.fca.org.uk/artefacts/NSM/DirectUpload/2026.pdf",
        ),
        NSMCandidate(
            title="ESEF Tagged Annual Report and Audited Accounts",
            date_text="13/03/2026 11:58",
            organisation_name="THE GYM GROUP PLC",
            category="Annual Financial Report",
            href=None,
        ),
        NSMCandidate(
            title="Annual Report and Audited Accounts",
            date_text="14/03/2025 09:06",
            organisation_name="THE GYM GROUP PLC",
            category="Annual Financial Report",
            href="https://data.fca.org.uk/artefacts/NSM/DirectUpload/2025.pdf",
        ),
    ]

    selected = NSMDownloadService._group_annual_candidates_by_year(candidates, years=5)

    assert [item.year for item in selected] == [2026, 2025]
    assert selected[0].selected_candidate.title == "ESEF Tagged Annual Report and Audited Accounts"
    assert selected[1].selected_candidate.href == "https://data.fca.org.uk/artefacts/NSM/DirectUpload/2025.pdf"


def test_annual_history_missing_years_reports_gaps():
    selected = NSMDownloadService._group_annual_candidates_by_year(
        [
            NSMCandidate(
                title="Annual Report",
                date_text="13/03/2026 12:04",
                organisation_name="THE GYM GROUP PLC",
                category="Annual Financial Report",
                href="https://example.test/2026.pdf",
            ),
            NSMCandidate(
                title="Annual Report",
                date_text="14/03/2024 09:06",
                organisation_name="THE GYM GROUP PLC",
                category="Annual Financial Report",
                href="https://example.test/2024.pdf",
            ),
        ],
        years=3,
    )

    missing = NSMDownloadService._missing_years(selected, years=3)

    assert missing == [2025]
