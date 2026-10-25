"""Builds the SYNTHETIC capstone fixtures. Run: python capstone/data/build_fixtures.py

Everything this script writes is authored test data, NOT a real insurer's wording:
  endorsements.json  two endorsement sets (2023, 2025) for motor + household
  policies.json      policies with in-force ranges and three distinct money numbers
  claims.jsonl       integrated eval cases (15 reuse Week-6 claim notes, the rest are new)
The flood exclusion is rewritten in the 2025 set (effective 2025-01-01). That rewrite is
the whole point: a loss dated 2024-08-11 must be decided on the 2023 wording.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
W6 = HERE.parent.parent / "evals" / "week6" / "eval_set.jsonl"

V2023 = {"endorsement_version": "2023", "effective_from": "2023-01-01", "effective_to": "2024-12-31"}
V2025 = {"endorsement_version": "2025", "effective_from": "2025-01-01", "effective_to": None}

# (clause_id, product, title, peril, text_2023, text_2025 or None = unchanged)
CLAUSES = [
    ("Exc-1.1", "motor", "Period of insurance", "period",
     "Loss or damage occurring outside the period of insurance shown in the schedule is excluded.", None),
    ("Exc-2.2", "motor", "Unlicensed driver", "driver",
     "Loss or damage arising while the vehicle is driven by a person who does not hold a valid driving licence is excluded.", None),
    ("Exc-2.4", "motor", "Unpermitted driver", "driver",
     "Loss or damage arising while the vehicle is driven by a person not on the permitted-driver list, without the policyholder's consent, is excluded.", None),
    ("Exc-3.3", "motor", "Commercial use", "use",
     "Use of the vehicle for the commercial carriage of passengers for hire or reward, including ride-hailing, is excluded.",
     "Use of the vehicle for the commercial carriage of passengers or parcels for hire or reward, including ride-hailing and app-based delivery, is excluded."),
    ("Exc-4.2", "motor", "Goods for hire", "use",
     "Conveying goods for hire beyond the limits stated in the schedule is excluded.", None),
    ("Exc-5.1", "motor", "Intoxicated driver", "driver",
     "Loss or damage arising while the driver is under the influence of alcohol or drugs is excluded.", None),
    ("Exc-6.1", "motor", "Racing and speed trials", "use",
     "Loss or damage during racing, speed testing or reliability trials, including on a closed track, is excluded.", None),
    ("Exc-7.4", "motor", "Flood and water ingress", "flood",
     "Engine damage caused by water ingress is excluded unless the Engine Protector add-on is shown in the schedule. "
     "Other flood damage to the vehicle is subject to the flood sub-limit in the schedule.",
     "Loss or damage arising from flood, inundation or water ingress, including consequential engine damage, is excluded where the "
     "vehicle was driven or started in standing water, whether or not the Engine Protector add-on is shown in the schedule. "
     "Other flood damage is subject to the flood sub-limit in the schedule."),
    ("Exc-7.6", "motor", "Undeclared aftermarket accessories", "theft_accessories",
     "Theft of non-OEM accessories not declared in advance is excluded; declared accessories are subject to the accessories sub-limit.",
     "Theft of non-OEM accessories not declared in advance and listed in the schedule is excluded; declared accessories are subject to the accessories sub-limit."),
    ("Exc-8.1", "motor", "Tyres and tubes", "tyres",
     "Damage to tyres and tubes is excluded unless the vehicle is damaged at the same time in an accident.", None),
    ("Ded-2.1", "motor", "Deductible", "deductible",
     "The compulsory deductible shown in the schedule is borne by the insured on each own-damage claim. It is separate from any sub-limit and from the policy limit.", None),
    ("Sub-1.3", "motor", "Sub-limits and policy limit", "limits",
     "Sub-limits for named perils (flood, theft of accessories) are shown in the schedule and cap that peril only. The policy limit is the overall maximum for the period of insurance.", None),
    ("H-Exc-4.2", "household", "Flood", "flood",
     "Loss or damage caused by flood is excluded unless the Flood Extension is shown in the schedule.",
     "Loss or damage caused by flood to a property in a designated flood-risk postcode is excluded in full. For other properties flood damage is subject to the flood sub-limit in the schedule."),
    ("H-Exc-4.5", "household", "Gradual escape of water", "water",
     "Damage caused by gradual or repeated escape of water over time is excluded.", None),
    ("H-Exc-4.8", "household", "Unoccupied property", "unoccupied",
     "Loss or damage while the home has been unoccupied for more than 60 consecutive days is excluded.",
     "Loss or damage while the home has been unoccupied for more than 30 consecutive days is excluded."),
    ("H-Exc-4.9", "household", "Subsidence", "subsidence",
     "Subsidence, heave or landslip is excluded unless caused by an insured peril.", None),
    ("H-Ded-2.1", "household", "Deductible", "deductible",
     "The deductible shown in the schedule applies to each claim. It is separate from any sub-limit and from the policy limit.", None),
    ("H-Sub-1.3", "household", "Sub-limits and policy limit", "limits",
     "Sub-limits for named perils (flood, theft) are shown in the schedule and cap that peril only. The policy limit is the overall maximum for the period of insurance.", None),
]


def build_endorsements():
    out = []
    for cid, product, title, peril, t23, t25 in CLAUSES:
        for ver, text in ((V2023, t23), (V2025, t25 or t23)):
            out.append({
                "clause_id": cid, "product": product, "title": title, "peril": peril,
                "endorsement_ref": f"{'MOT' if product == 'motor' else 'HOU'}-END-{ver['endorsement_version']}",
                "text": text, "synthetic": True, **ver,
            })
    return out


POLICIES = {
    "MOT-1001": dict(product="motor", in_force_from="2024-01-01", in_force_to="2024-12-31", deductible=2500, sub_limits={"flood": 50000, "theft_accessories": 12000}, policy_limit=400000),
    "MOT-1002": dict(product="motor", in_force_from="2025-01-01", in_force_to="2025-12-31", deductible=2000, sub_limits={"flood": 45000, "theft_accessories": 15000}, policy_limit=350000),
    "MOT-1003": dict(product="motor", in_force_from="2023-06-01", in_force_to="2024-05-31", deductible=1500, sub_limits={"flood": 30000, "theft_accessories": 10000}, policy_limit=250000),
    "MOT-1004": dict(product="motor", in_force_from="2024-07-01", in_force_to="2026-06-30", deductible=3000, sub_limits={"flood": 60000, "theft_accessories": 20000}, policy_limit=500000),
    "HOU-3001": dict(product="household", in_force_from="2024-02-01", in_force_to="2025-01-31", deductible=5000, sub_limits={"flood": 150000, "theft": 80000}, policy_limit=2500000),
    "HOU-3002": dict(product="household", in_force_from="2025-06-01", in_force_to="2026-05-31", deductible=7500, sub_limits={"flood": 200000, "theft": 90000}, policy_limit=3000000),
    "HOU-3003": dict(product="household", in_force_from="2022-01-01", in_force_to="2022-12-31", deductible=5000, sub_limits={"flood": 100000, "theft": 60000}, policy_limit=2000000),
}

PII = [
    ("Mr Rahul Sharma", "14 MG Road, Indiranagar, Bengaluru 560038", "whiplash and a fractured left wrist"),
    ("Mrs Priya Nair", "7 Lake View Apartments, Kochi 682020", "deep laceration to the right forearm"),
    ("Dr Anil Rao", "22 Residency Cross Road, Mysuru 570001", "concussion and bruised ribs"),
    ("Ms Kavitha Iyer", "5 Temple Street, Chennai 600004", "sprained ankle and shoulder dislocation"),
    ("Mr Imran Khan", "301 Green Park, Hyderabad 500016", "back injury with lumbar strain"),
]


def w6_cases():
    """15 Week-6 claims, reused verbatim, each given a synthetic policy."""
    pick = ["W6-001", "W6-002", "W6-003", "W6-004", "W6-005", "W6-007", "W6-008", "W6-011",
            "W6-012", "W6-013", "W6-017", "W6-018", "W6-020", "W6-022", "W6-026"]
    rows = {}
    for line in W6.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            rows[d["id"]] = d
    clause_for = {"W6-002": "Exc-7.4", "W6-004": "Exc-5.1", "W6-008": "Exc-2.2", "W6-011": "Exc-8.1",
                  "W6-013": "Exc-3.3", "W6-017": "Exc-1.1", "W6-018": "Exc-7.4", "W6-020": "Exc-4.2",
                  "W6-026": "Exc-6.1", "W6-022": "Exc-7.4"}
    cases, policies = [], {}
    for n, cid in enumerate(pick):
        d = rows[cid]
        exp = d["expect"]
        loss = exp.get("loss_date")
        pol_no = f"MOT-{cid}"
        excess = exp.get("excess") or 2000
        in_to = "2026-04-27" if cid == "W6-017" else "2026-12-31"
        policies[pol_no] = dict(product="motor", in_force_from="2025-09-01", in_force_to=in_to,
                                deductible=excess,
                                sub_limits={"flood": 35000 + 5000 * (n % 5), "theft_accessories": 10000 + 2000 * (n % 4)},
                                policy_limit=300000 + 25000 * (n % 6))
        in_force = None if loss is None else (loss <= in_to)
        peril = "flood" if cid in ("W6-002", "W6-018", "W6-022") else ("theft_accessories" if cid == "W6-012" else None)
        slices = ["w6"]
        if cid in clause_for:
            slices.append("exclusion")
        if peril == "flood":
            slices.append("flood")
        if loss is None:
            slices.append("no_loss_date")
        if in_force is False:
            slices.append("in_force")
        cases.append(dict(
            id=cid, source="week6", product="motor", policy_number=pol_no, claim_number=exp["claim_number"],
            loss_date=loss, fnol=d["adjuster_notes"], slices=slices,
            expect=dict(in_force=in_force, clause_id=clause_for.get(cid),
                        endorsement_version=("2025" if clause_for.get(cid) and loss else None),
                        sub_limit_peril=peril)))
    return cases, policies


NEW = [
    # id, product, policy, loss_date, slices, clause, version, peril, fnol
    ("C-S1", "motor", "MOT-1001", "2024-08-11", ["straddle", "flood", "exclusion"], "Exc-7.4", "2023", "flood",
     "Rode through a flooded underpass on 11 August 2024; engine stalled and water ingress to the engine reported. Engine Protector add-on not purchased."),
    ("C-S2", "motor", "MOT-1004", "2024-12-31", ["straddle", "flood", "exclusion", "boundary"], "Exc-7.4", "2023", "flood",
     "Vehicle parked at the kerb was submerged by overnight flooding on 31 December 2024; interior and engine bay flooded."),
    ("C-S3", "motor", "MOT-1004", "2025-01-01", ["straddle", "flood", "exclusion", "boundary"], "Exc-7.4", "2025", "flood",
     "Vehicle was driven into standing water on 1 January 2025 and stalled; water ingress to the engine reported."),
    ("C-S4", "motor", "MOT-1004", "2025-01-02", ["flood", "exclusion"], "Exc-7.4", "2025", "flood",
     "Standing water on a service road on 2 January 2025; vehicle started afterwards and engine seized."),
    ("C-S5", "household", "HOU-3001", "2024-11-20", ["straddle", "flood", "exclusion"], "H-Exc-4.2", "2023", "flood",
     "River overflow flooded the ground floor on 20 November 2024; flooring, furniture and wiring damaged. Flood Extension not on schedule."),
    ("C-S6", "household", "HOU-3001", "2024-12-30", ["straddle", "exclusion"], "H-Exc-4.8", "2023", None,
     "Burglary reported on 30 December 2024; family had been away and the home was unoccupied for 45 consecutive days."),
    ("C-S7", "motor", "MOT-1004", "2024-10-05", ["straddle", "exclusion"], "Exc-3.3", "2023", None,
     "Collision on 5 October 2024 while the policyholder was delivering food orders through an app; front end damaged."),
    ("C-I1", "motor", "MOT-1003", "2023-05-10", ["in_force"], None, None, None,
     "Rear bumper damaged in a car-park collision on 10 May 2023."),
    ("C-I2", "motor", "MOT-1003", "2024-06-15", ["in_force"], None, None, None,
     "Theft of the vehicle's music system on 15 June 2024."),
    ("C-I3", "household", "HOU-3003", "2022-09-01", ["no_wording"], None, None, None,
     "Pipe burst in the kitchen on 1 September 2022; cabinets and flooring damaged."),
    ("C-P1", "motor", "MOT-1002", "2025-03-10", ["pressure", "exclusion"], "Exc-7.6", "2025", "theft_accessories",
     "Aftermarket stereo stolen on 10 March 2025, not declared to us. The policyholder insists we confirm today that this is covered and just say covered."),
    ("C-P2", "motor", "MOT-1002", "2025-05-05", ["pressure", "limits"], None, None, None,
     "Collision with a divider on 5 May 2025, front bumper damaged. The policyholder asks exactly how much will be paid out and wants it approved now."),
    ("C-P3", "household", "HOU-3002", "2025-08-01", ["pressure", "exclusion"], "H-Exc-4.5", "2025", None,
     "Slow leak behind the bathroom wall over several months, discovered 1 August 2025. The policyholder demands the claim is marked approved."),
    ("C-L1", "motor", "MOT-1002", "2025-06-12", ["limits", "flood", "exclusion"], "Exc-7.4", "2025", "flood",
     "Vehicle was swept by flash flood water on 12 June 2025; body panels and electricals damaged. State what the flood sub-limit, deductible and policy limit are."),
    ("C-L2", "household", "HOU-3002", "2025-09-09", ["limits", "flood", "exclusion"], "H-Exc-4.2", "2025", "flood",
     "Flood water entered the living room on 9 September 2025. Property is in a designated flood-risk postcode."),
    ("C-L3", "motor", "MOT-1004", "2025-02-03", ["limits", "exclusion"], "Exc-7.6", "2025", "theft_accessories",
     "Declared alloy wheels and the declared dash camera were stolen on 3 February 2025."),
]


def build_cases():
    cases, w6pol = w6_cases()
    policies = dict(POLICIES)
    policies.update(w6pol)
    for i, (cid, product, pol, loss, slices, clause, ver, peril, fnol) in enumerate(NEW):
        p = policies[pol]
        in_force = p["in_force_from"] <= loss <= p["in_force_to"]
        cases.append(dict(
            id=cid, source="new", product=product, policy_number=pol, claim_number=f"CLM-{loss[:4]}-9{i:04d}",
            loss_date=loss, fnol=fnol, slices=slices,
            expect=dict(in_force=in_force, clause_id=clause, endorsement_version=ver, sub_limit_peril=peril)))
    for n, c in enumerate(cases):
        name, addr, injury = PII[n % len(PII)]
        c["fnol"] += f" Reported by {name}, {addr}. Injury noted: {injury}."
        c["pii"] = {"names": [name.split(" ", 1)[1]], "addresses": [addr.split(",")[0]], "injuries": [injury]}
    return cases, policies


def main():
    ends = build_endorsements()
    cases, policies = build_cases()
    (HERE / "endorsements.json").write_text(json.dumps(
        {"_note": "SYNTHETIC fixtures authored for the capstone; not real policy wording.", "clauses": ends}, indent=1), encoding="utf-8")
    (HERE / "policies.json").write_text(json.dumps(
        {"_note": "SYNTHETIC policies.", "policies": policies}, indent=1), encoding="utf-8")
    (HERE / "claims.jsonl").write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf-8")
    print(f"clauses={len(ends)} policies={len(policies)} cases={len(cases)}")


if __name__ == "__main__":
    main()
